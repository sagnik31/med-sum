"""
What is running right now: the pipeline run and the step (span) of the
current thread or task, kept in context variables.

This module has no database code, so llm.py can report each model call to
the active step without importing the tracer. Outside a step, reporting
does nothing.
"""

from contextvars import ContextVar
from dataclasses import dataclass

# Set by tracing.Run while a run is open, and by the node wrapper while a
# node runs. Context variables follow the code into threads started with
# contextvars.copy_context(), which is how LangGraph runs sync nodes.
current_run: ContextVar = ContextVar("medsum_current_run", default=None)
current_step: ContextVar = ContextVar("medsum_current_step", default=None)


@dataclass(frozen=True)
class ModelCall:
    """One model reply as llm.py saw it. No prompt or reply text."""

    model: str
    role: str  # primary or fallback
    prompt_tokens: int | None
    completion_tokens: int | None
    complete: bool  # False when the reply hit the token cap


def record_model_call(call: ModelCall) -> None:
    """Attach a model call to the active step, if there is one."""
    step = current_step.get()
    if step is not None:
        step.add_model_call(call)


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
