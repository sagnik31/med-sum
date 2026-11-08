"""
Model calls with a primary and a fallback model (both local, via Ollama).

The same request goes to the fallback when the primary raises an error or
stops because it hit the token cap. Output that hit the cap is never returned:
if both models fail, ModelOutputError is raised so the job is marked failed
instead of saving a cut-off result.

Each reply that comes back (complete or capped) is reported to the active
pipeline step, if any, with its model, role and token counts (section 8.4).
"""

import logging

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


class ModelOutputError(RuntimeError):
    """Neither model produced complete output."""


def chat(
    messages: list[dict],
    *,
    max_tokens: int,
    stop_marker: str | None = None,
    verbose: bool = False,
) -> str:
    """
    Run `messages` on the primary model, then the fallback if needed, and
    return the reply text.

    If `stop_marker` is given, generation stops as soon as it appears and the
    text before it is returned.
    """
    failures = []
    for model, role in ((PRIMARY_MODEL, "primary"), (FALLBACK_MODEL, "fallback")):
        try:
            text, complete, prompt_tokens, completion_tokens = _stream(
                model, messages, max_tokens, stop_marker, verbose
            )
        except (ollama.ResponseError, ConnectionError, httpx.TimeoutException) as e:
            failures.append(f"{model}: {e}")
            logger.warning("Model %s failed: %s", model, e)
            continue
        record_model_call(
            ModelCall(model, role, prompt_tokens, completion_tokens, complete)
        )
        if complete:
            if model != PRIMARY_MODEL:
                logger.warning("Used fallback model %s", model)
            return text
        failures.append(f"{model}: output hit the {max_tokens}-token cap")
        logger.warning("Model %s hit the %d-token cap", model, max_tokens)
    raise ModelOutputError("No model produced complete output. " + "; ".join(failures))


def _stream(
    model: str,
    messages: list[dict],
    max_tokens: int,
    stop_marker: str | None,
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
    for chunk in _client.chat(
        model=model,
        messages=messages,
        stream=True,
        think=False,
        options={**GENERATION_OPTIONS, "num_ctx": NUM_CTX, "num_predict": max_tokens},
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
