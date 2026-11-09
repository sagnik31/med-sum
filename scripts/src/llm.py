"""
Model calls with a primary and a fallback model (both local, via Ollama).

The same request goes to the fallback when the primary raises an error or
stops because it hit the token cap. Output that hit the cap is never returned:
if both models fail, ModelOutputError is raised so the job is marked failed
instead of saving a cut-off result.

Every round trip, including one that raised, is reported to the active
pipeline step, if any, as a ModelCall: model, digest, role, request options,
status, Ollama's token counts and timings, our own first-token and wall
times, and the sizes of what was sent and received (characters, image count
and pixel sizes). Never the prompt, the reply or the image bytes.

A caller can start at the fallback (`model_role="fallback"`), e.g. when the
graph re-runs a parse the primary kept getting wrong; there is then no
model after it. `format` is Ollama's structured output: a JSON schema the
reply is constrained to.
"""

import base64
import binascii
import logging
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import ollama

from config import FALLBACK_MODEL, NUM_CTX, PRIMARY_MODEL
from observability import model_calls
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

PROVIDER = "ollama"

# Errors that send the request on to the fallback. Anything else is a bug
# in the request (or elsewhere) and is raised at once, after being recorded.
MODEL_ERRORS = (ollama.ResponseError, ConnectionError, httpx.TimeoutException)

ModelRole = Literal["primary", "fallback"]
_MODELS = {"primary": PRIMARY_MODEL, "fallback": FALLBACK_MODEL}

# Model digests, looked up at most once per model per process (a tag can
# point to new weights after a re-pull). None when the lookup failed.
_digests: dict[str, str | None] = {}


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

    If `stop_marker` is given, the text before it is returned, and Ollama is
    asked to stop generating there (its `stop` option). `format` (a JSON
    schema) constrains the reply.
    """
    if model_role not in _MODELS:
        raise ValueError(f"unknown model role {model_role!r}")
    roles = ("primary", "fallback") if model_role == "primary" else ("fallback",)
    options = {**GENERATION_OPTIONS, "num_ctx": NUM_CTX, "num_predict": max_tokens}
    if stop_marker:
        options["stop"] = [stop_marker]
    request = {**options, "think": False, "structured_output": format is not None}
    sizes = _message_sizes(messages)
    failures = []
    for role in roles:
        model = _MODELS[role]
        attempt = _Attempt()
        try:
            _stream(attempt, model, messages, options, format, verbose, stop_marker)
        except Exception as e:
            _report(attempt, model, role, request, sizes, error=e)
            if not isinstance(e, MODEL_ERRORS):
                raise
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
        text, complete = _report(attempt, model, role, request, sizes, stop_marker)
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


class _Attempt:
    """What one round trip has seen so far; filled in while it streams."""

    def __init__(self):
        self.started_at = datetime.now(timezone.utc)
        self.started = time.monotonic()
        self.first_token_ms: int | None = None
        self.raw = ""  # everything streamed, before cutting at a stop marker
        self.done = None  # the final chunk, which carries counts and timings


def _stream(attempt, model, messages, options, format, verbose, stop_marker):
    """
    Read the whole streamed reply into `attempt`, up to Ollama's final
    chunk: only that chunk carries the token counts and timings, so the
    stream is read to the end even after a stop marker appears.
    """
    extra = {"format": format} if format is not None else {}
    printing = verbose
    for chunk in _client.chat(
        model=model,
        messages=messages,
        stream=True,
        think=False,
        options=options,
        **extra,
    ):
        token = chunk.message.content or ""
        if token and attempt.first_token_ms is None:
            attempt.first_token_ms = _ms_since(attempt.started)
        attempt.raw += token
        if printing and token:
            print(token, end="", flush=True)
            printing = not (stop_marker and stop_marker in attempt.raw)
        if chunk.done:
            attempt.done = chunk


def _report(attempt, model, role, request, sizes, stop_marker=None, error=None):
    """
    Record the round trip as a ModelCall and return (text, complete): the
    reply cut at the stop marker (if it appeared) and stripped, and whether
    it is usable (not capped). A reply that contains the marker is complete
    even if generation then ran on to the cap, as before Ollama's `stop`
    option was sent.
    """
    wall_ms = _ms_since(attempt.started)
    ended_at = datetime.now(timezone.utc)
    done = attempt.done if error is None else None
    done_reason = getattr(done, "done_reason", None)
    raw = attempt.raw
    if error is not None:
        text, complete, status = "", False, "failed"
    elif stop_marker and stop_marker in raw:
        text, complete, status = raw.split(stop_marker)[0].strip(), True, "stopped"
    else:
        text, complete = raw.strip(), done_reason != "length"
        # Ollama trims a matched stop string and ends with done_reason
        # "stop", the same as the model's own end: "stopped" covers both.
        status = "capped" if not complete else "stopped" if stop_marker else "ok"
    input_tokens = _count(done, "prompt_eval_count")
    num_ctx, num_predict = request["num_ctx"], request["num_predict"]
    call = ModelCall(
        model=model,
        role=role,
        prompt_tokens=input_tokens,
        completion_tokens=_count(done, "eval_count"),
        complete=complete,
        status=status,
        provider=PROVIDER,
        model_digest=model_digest(model),
        request=request,
        done_reason=done_reason,
        error_type=type(error).__name__ if error is not None else None,
        total_ms=_ns_to_ms(done, "total_duration"),
        load_ms=_ns_to_ms(done, "load_duration"),
        prompt_eval_ms=_ns_to_ms(done, "prompt_eval_duration"),
        eval_ms=_ns_to_ms(done, "eval_duration"),
        first_token_ms=attempt.first_token_ms,
        wall_ms=wall_ms,
        input_chars=sizes.chars,
        output_chars=len(raw),
        image_count=len(sizes.images),
        image_pixels=sizes.images,
        prompt_truncated=prompt_truncated(input_tokens, num_ctx, num_predict),
        started_at=attempt.started_at,
        ended_at=ended_at,
    )
    record_model_call(call)
    logger.info(
        "model call ended: %s %s",
        model,
        status,
        extra={"obs": model_calls.log_fields(call)},
    )
    return text, complete


def prompt_truncated(
    input_tokens: int | None, num_ctx: int, num_predict: int
) -> bool | None:
    """
    A heuristic: True when the prompt left less room than num_predict in
    the context window. Ollama is believed to cut an over-long prompt and
    say so only in its server log, so a cut prompt shows up as a count close
    to num_ctx; a prompt this long also leaves the reply no room to reach
    num_predict. None without a count.
    """
    if input_tokens is None:
        return None
    return input_tokens >= num_ctx - num_predict


def model_digest(model: str) -> str | None:
    """
    The digest of the weights a model tag points to, from Ollama's model
    list, looked up once per model per process. Never fails a model call:
    any error leaves it None.
    """
    if model in _digests:
        return _digests[model]
    digest = None
    try:
        for entry in _client.list().models:
            name = entry.model or ""
            if name == model or name == f"{model}:latest":
                digest = entry.digest
                break
    except Exception as e:
        logger.warning(
            "could not look up the digest of %s: %s",
            model,
            type(e).__name__,
            extra={"obs": {"event": "model_digest_failed", "model": model,
                           "error_type": type(e).__name__}},
        )  # fmt: skip
    _digests[model] = digest
    return digest


# -------------------------------------------------------------------
# Sizes of what was sent: characters and image pixels, no content
# -------------------------------------------------------------------
class _Sizes:
    def __init__(self, chars: int, images: tuple):
        self.chars = chars
        self.images = images  # (width, height) or None per image


def _message_sizes(messages: list[dict]) -> _Sizes:
    chars = 0
    images = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        for image in message.get("images") or ():
            images.append(image_size(image))
    return _Sizes(chars, tuple(images))


def image_size(image) -> tuple[int, int] | None:
    """
    (width, height) of a PNG or JPEG image given as Ollama accepts it:
    bytes, a path, a base64 string or an ollama.Image. Reads only the
    header; None for anything it doesn't recognise.
    """
    if isinstance(image, ollama.Image):
        image = image.value
    try:
        if isinstance(image, Path):
            data = image.read_bytes()
        elif isinstance(image, str):
            path = Path(image) if len(image) < 1024 else None
            if path is not None and path.is_file():
                data = path.read_bytes()
            else:
                data = base64.b64decode(image[:65536], validate=False)
        else:
            data = bytes(image)
    except (OSError, ValueError, TypeError, binascii.Error):
        return None
    return _png_size(data) or _jpeg_size(data)


def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return None


# JPEG start-of-frame markers (baseline, progressive, ...), which carry the size.
_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD,
                0xCE, 0xCF}  # fmt: skip


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # padding
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length field
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in _SOF_MARKERS:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None


# -------------------------------------------------------------------
# Units
# -------------------------------------------------------------------
def _count(done, name: str) -> int | None:
    value = getattr(done, name, None)
    return value if isinstance(value, int) else None


def _ns_to_ms(done, name: str) -> int | None:
    """Ollama's durations are in nanoseconds."""
    value = getattr(done, name, None)
    return round(value / 1_000_000) if isinstance(value, int) else None


def _ms_since(start: float) -> int:
    return round((time.monotonic() - start) * 1000)
