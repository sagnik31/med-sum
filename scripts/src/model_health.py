"""
Model health: a circuit breaker per model, and counts of quality failures.

Only infrastructure failures count toward the breaker: an error or timeout
from the model server (llm.MODEL_ERRORS). After FAILURES of them in a row
the model is **open**: llm.chat skips it, so a dead primary costs nothing
and the request goes straight to the fallback. After COOLDOWN_S it is
**half-open**: the next call is let through as a probe (one at a time).
The probe succeeding closes the breaker; failing opens it again for
another cool-down. Any reply, even one cut off at the token cap, is a
success here: the model answered.

Quality failures (a parse that doesn't fit its schema, or that the
validators find errors in) are counted per model and logged, for
diagnosis, and never trip the breaker: they say nothing about whether the
server is up (decision 46).

State lives in memory, per process, behind one lock: the worker thread
and request threads share it. Log events model_disabled, model_probe and
model_restored carry only the model, its role, the failure count, the
cool-down and the error type.

    MEDSUM_BREAKER_FAILURES     consecutive failures that open it (3)
    MEDSUM_BREAKER_COOLDOWN_S   seconds it stays open (900)
"""

import logging
import os
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("model_health")

FAILURES = int(os.environ.get("MEDSUM_BREAKER_FAILURES", "3"))
COOLDOWN_S = float(os.environ.get("MEDSUM_BREAKER_COOLDOWN_S", "900"))

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

# The clock, a seam for tests.
now = time.monotonic


@dataclass
class _Breaker:
    state: str = CLOSED
    failures: int = 0  # consecutive infrastructure failures
    opened_at: float | None = None
    probing: bool = False  # a half-open probe is in flight
    last_error: str | None = None  # type name only
    quality_failures: dict | None = None  # kind -> count

    def __post_init__(self):
        self.quality_failures = self.quality_failures or {}


_lock = threading.Lock()
_breakers: dict[str, _Breaker] = {}


def reset() -> None:
    """Forget every model's state (tests; a restart does the same)."""
    with _lock:
        _breakers.clear()


def _get(model: str) -> _Breaker:
    return _breakers.setdefault(model, _Breaker())


def _log(level, message, event, model, role, b: _Breaker, **fields):
    logger.log(
        level,
        message,
        model,
        extra={"obs": {"event": event, "model": model, "model_role": role,
                       "failures": b.failures, "breaker": b.state, **fields}},
    )  # fmt: skip


def allow(model: str, role: str) -> bool:
    """
    Whether a call to `model` may go ahead now. An open breaker whose
    cool-down has passed lets this one call through as its probe.
    """
    with _lock:
        b = _get(model)
        if b.state == CLOSED:
            return True
        if b.state == OPEN and now() - b.opened_at >= COOLDOWN_S:
            b.state = HALF_OPEN
        if b.state == HALF_OPEN and not b.probing:
            b.probing = True
            _log(logging.INFO, "probing model %s", "model_probe", model, role, b)
            return True
        return False


def succeeded(model: str, role: str) -> None:
    """The model answered (complete or capped)."""
    with _lock:
        b = _get(model)
        restored = b.state != CLOSED
        b.state, b.failures, b.opened_at, b.probing = CLOSED, 0, None, False
        if restored:
            _log(logging.INFO, "model %s restored", "model_restored", model, role, b)


def failed(model: str, role: str, error: BaseException) -> None:
    """An infrastructure failure: the server errored or timed out."""
    with _lock:
        b = _get(model)
        b.failures += 1
        b.last_error = type(error).__name__
        # An open breaker stays as it is: a call let through just before
        # it opened doesn't restart the cool-down.
        if b.state == HALF_OPEN or (b.state == CLOSED and b.failures >= FAILURES):
            b.state, b.opened_at, b.probing = OPEN, now(), False
            _log(logging.ERROR, "model %s disabled", "model_disabled", model, role,
                 b, error_type=b.last_error, cooldown_s=COOLDOWN_S)  # fmt: skip


def abandoned(model: str) -> None:
    """
    A call ended with neither outcome (a bug raised): free the probe slot
    so the next call can probe, without changing the state.
    """
    with _lock:
        _get(model).probing = False


def quality_failure(model: str | None, role: str | None, kind: str) -> None:
    """
    The model answered, but the answer was unusable: `kind` is off_schema,
    capped or validation. Counted and logged; never trips the breaker.
    """
    if model is None:
        return
    with _lock:
        b = _get(model)
        b.quality_failures[kind] = b.quality_failures.get(kind, 0) + 1
        count = b.quality_failures[kind]
    logger.info(
        "quality failure of model %s: %s",
        model,
        kind,
        extra={"obs": {"event": "model_quality_failure", "model": model,
                       "model_role": role, "quality_kind": kind,
                       "quality_failures": count}},
    )  # fmt: skip


def retry_in(model: str) -> float | None:
    """Seconds until an open model may be probed; None when it isn't open."""
    with _lock:
        b = _breakers.get(model)
        if b is None or b.state != OPEN:
            return None
        return max(0.0, COOLDOWN_S - (now() - b.opened_at))


def snapshot() -> dict:
    """Every model's state, for the health endpoint."""
    with _lock:
        models = {
            model: {
                "state": b.state,
                "consecutive_failures": b.failures,
                "last_error_type": b.last_error,
                "retry_in_s": (
                    round(max(0.0, COOLDOWN_S - (now() - b.opened_at)))
                    if b.state == OPEN
                    else None
                ),
                "quality_failures": dict(b.quality_failures),
            }
            for model, b in _breakers.items()
        }
    return {"failures_to_open": FAILURES, "cooldown_s": COOLDOWN_S, "models": models}
