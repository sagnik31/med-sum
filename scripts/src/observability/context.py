"""
What is running right now: the pipeline run and the step (span) of the
current thread or task, kept in context variables.

This module has no database code, so llm.py can report each model call to
the active step without importing the tracer. Outside a step, reporting
does nothing.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Set by tracing.Run while a run is open, and by the node wrapper while a
# node runs. Context variables follow the code into threads started with
# contextvars.copy_context(), which is how LangGraph runs sync nodes.
current_run: ContextVar = ContextVar("medsum_current_run", default=None)
current_step: ContextVar = ContextVar("medsum_current_step", default=None)
# True only while the documents being processed are known to be synthetic
# (the eval dataset): the one case where llm.chat may send text to a cloud
# model. Set it with synthetic_documents().
synthetic_only: ContextVar = ContextVar("medsum_synthetic_only", default=False)


@contextmanager
def synthetic_documents():
    """Mark the calls made inside the block as being about synthetic documents."""
    token = synthetic_only.set(True)
    try:
        yield
    finally:
        synthetic_only.reset(token)


@dataclass(frozen=True)
class ModelCall:
    """
    One model round trip as llm.py saw it, including one that raised. No
    prompt or reply text: sizes, counts and timings only. The first five
    fields are what a step totals; the rest become a pipeline_model_calls
    row.
    """

    model: str
    role: str  # primary or fallback
    prompt_tokens: int | None  # input tokens (Ollama's prompt_eval_count)
    completion_tokens: int | None  # output tokens (Ollama's eval_count)
    complete: bool  # False when the reply hit the token cap or the call raised
    # ok, capped, stopped or failed; by default from `complete`.
    status: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # the span id
    provider: str = "ollama"
    model_digest: str | None = None
    request: dict = field(default_factory=dict)  # options sent, never the prompt
    done_reason: str | None = None
    error_type: str | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_ms: int | None = None
    load_ms: int | None = None
    prompt_eval_ms: int | None = None
    eval_ms: int | None = None
    first_token_ms: int | None = None
    wall_ms: int = 0
    input_chars: int = 0
    output_chars: int = 0
    image_count: int = 0
    image_pixels: tuple = ()  # (width, height) per image, None if unknown
    prompt_truncated: bool | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def __post_init__(self):
        if not self.status:
            object.__setattr__(self, "status", "ok" if self.complete else "capped")
        if self.started_at is None or self.ended_at is None:
            now = datetime.now(timezone.utc)
            object.__setattr__(self, "started_at", self.started_at or now)
            object.__setattr__(self, "ended_at", self.ended_at or now)


def record_model_call(call: ModelCall) -> None:
    """Attach a model call to the active step, if there is one."""
    step = current_step.get()
    if step is not None:
        step.add_model_call(call)


def record_breaker_switch(event: str, model: str, role: str) -> None:
    """
    A circuit breaker switched a model off (model_disabled) or back on
    (model_restored) during a call of the active run, if there is one.
    """
    run = current_run.get()
    if run is not None:
        run.note_breaker_switch(event, model, role)


def current_ids() -> dict[str, str]:
    """
    Ids of the active run and step, for log lines. Ids and node names only;
    never anything read from a document.
    """
    ids: dict[str, str] = {}
    run = current_run.get()
    step = current_step.get()
    if step is not None:
        run = step.run
    if run is not None:
        ids["run_id"] = str(run.id)
        ids["trace_id"] = run.trace_id
        ids["document_id"] = str(run.document_id)
    if step is not None:
        ids["step_id"] = str(step.id)
        ids["span_id"] = str(step.id)
        if step.parent_id is not None:
            ids["parent_span_id"] = str(step.parent_id)
        ids["node"] = step.node
    return ids
