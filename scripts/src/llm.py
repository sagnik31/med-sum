"""
Model calls with a primary and a fallback model (both local, via Ollama).

The same request goes to the fallback when the primary raises an error or
stops because it hit the token cap. Output that hit the cap is never returned:
if both models fail, ModelOutputError is raised so the job is marked failed
instead of saving a cut-off result.

Each reply that comes back (complete or capped) is reported to the active
pipeline step, if any, with its model, role and token counts (section 8.4).

A caller can start at the fallback (`model_role="fallback"`), e.g. when the
graph re-runs a parse the primary kept getting wrong; there is then no
model after it. `format` is Ollama's structured output: a JSON schema the
reply is constrained to.
"""

import logging
from typing import Literal

import httpx
import ollama

from config import FALLBACK_MODEL, NUM_CTX, PRIMARY_MODEL
from observability.context import ModelCall, record_model_call

logger = logging.getLogger("llm")

# Longest wait for Ollama to send anything (connect, or the next chunk of a
# streamed reply). Generous because on a CPU a page image can take minutes
# before the first token; it stops a stalled server from hanging a job.
MODEL_TIMEOUT_SECONDS = 600
_client = ollama.Client(timeout=MODEL_TIMEOUT_SECONDS)

# Deterministic output: these calls copy and condense clinical text, so there
# is nothing to gain from sampling. presence_penalty is reset because Qwen's
# default (1.5) penalises tokens that repeat, and reports legitimately repeat
# units and flags.
GENERATION_OPTIONS = {"temperature": 0, "presence_penalty": 0}


ModelRole = Literal["primary", "fallback"]
_MODELS = {"primary": PRIMARY_MODEL, "fallback": FALLBACK_MODEL}


class ModelOutputError(RuntimeError):
    """Neither model produced complete output."""


def model_for(role: ModelRole) -> str:
    """The configured model for a role."""
    return _MODELS[role]


def chat(
    messages: list[dict],
    *,
    max_tokens: int,
    stop_marker: str | None = None,
    format: dict | None = None,
    model_role: ModelRole = "primary",
    verbose: bool = False,
) -> str:
    """
    Run `messages` on the primary model, then the fallback if needed, and
    return the reply text. With model_role="fallback" only the fallback runs.

    If `stop_marker` is given, generation stops as soon as it appears and the
    text before it is returned. `format` (a JSON schema) constrains the reply.
    """
    if model_role not in _MODELS:
        raise ValueError(f"unknown model role {model_role!r}")
    roles = ("primary", "fallback") if model_role == "primary" else ("fallback",)
    failures = []
    for role in roles:
        model = _MODELS[role]
        try:
            text, complete, prompt_tokens, completion_tokens = _stream(
                model, messages, max_tokens, stop_marker, format, verbose
            )
        except (ollama.ResponseError, ConnectionError, httpx.TimeoutException) as e:
            failures.append(f"{model}: {e}")
            # The type only: the message stays in the exception for the caller.
            logger.warning(
                "model %s failed: %s",
                model,
                type(e).__name__,
                extra={"obs": {"event": "model_failed", "model": model,
                               "model_role": role, "error_type": type(e).__name__}},
            )  # fmt: skip
            continue
        record_model_call(
            ModelCall(model, role, prompt_tokens, completion_tokens, complete)
        )
        if complete:
            if role == "fallback":
                logger.warning(
                    "used the fallback model %s",
                    model,
                    extra={"obs": {"event": "fallback_used", "model": model,
                                   "model_role": role}},
                )  # fmt: skip
            return text
        failures.append(f"{model}: output hit the {max_tokens}-token cap")
        logger.warning(
            "model %s hit the %d-token cap",
            model,
            max_tokens,
            extra={"obs": {"event": "output_capped", "model": model,
                           "model_role": role}},
        )  # fmt: skip
    raise ModelOutputError("No model produced complete output. " + "; ".join(failures))


def _stream(
    model: str,
    messages: list[dict],
    max_tokens: int,
    stop_marker: str | None,
    format: dict | None,
    verbose: bool,
) -> tuple[str, bool, int | None, int | None]:
    """
    Return (text, complete, prompt_tokens, completion_tokens); complete is
    False when the token cap was hit. Ollama sends the token counts with the
    last chunk, so they are None when a stop marker ends the reply early.
    """
    text = ""
    done_reason = None
    prompt_tokens = completion_tokens = None
    extra = {"format": format} if format is not None else {}
    for chunk in _client.chat(
        model=model,
        messages=messages,
        stream=True,
        think=False,
        options={**GENERATION_OPTIONS, "num_ctx": NUM_CTX, "num_predict": max_tokens},
        **extra,
    ):
        token = chunk.message.content or ""
        text += token
        if verbose and token:
            print(token, end="", flush=True)
        if stop_marker and stop_marker in text:
            return text.split(stop_marker)[0].strip(), True, None, None
        if chunk.done:
            done_reason = chunk.done_reason
            prompt_tokens = getattr(chunk, "prompt_eval_count", None)
            completion_tokens = getattr(chunk, "eval_count", None)
    return text.strip(), done_reason != "length", prompt_tokens, completion_tokens
