"""
pipeline_model_calls: one row per model round trip made by llm.chat, a
child span of its pipeline step.

Column names follow this project's style. OTEL_GENAI_ATTRIBUTES maps the
OpenTelemetry GenAI semantic conventions onto them, so an OTLP exporter
later is a straight mapping: each value is a SQL expression over one row.

The rows are written by tracing.Run when the step ends, in the same
transaction as the step's totals, so the totals always equal the rows.
"""

from psycopg.types.json import Jsonb

from observability.jsonable import jsonable

OTEL_GENAI_ATTRIBUTES = {
    "gen_ai.operation.name": "'chat'",
    "gen_ai.provider.name": "provider",
    "gen_ai.request.model": "model",
    # Ollama answers with the model it was asked for.
    "gen_ai.response.model": "model",
    "gen_ai.request.temperature": "(request ->> 'temperature')::float8",
    "gen_ai.request.max_tokens": "(request ->> 'num_predict')::integer",
    "gen_ai.response.finish_reasons": "ARRAY[done_reason]",
    "gen_ai.usage.input_tokens": "input_tokens",
    "gen_ai.usage.output_tokens": "output_tokens",
    "error.type": "error_type",
}

COLUMNS = (
    "id", "step_id", "run_id", "seq", "started_at", "ended_at", "provider",
    "model", "model_digest", "role", "request", "status", "done_reason",
    "error_type", "input_tokens", "output_tokens", "cached_input_tokens",
    "reasoning_tokens", "total_ms", "load_ms", "prompt_eval_ms", "eval_ms",
    "first_token_ms", "wall_ms", "input_chars", "output_chars", "image_count",
    "image_pixels", "prompt_truncated",
)  # fmt: skip

INSERT = "INSERT INTO pipeline_model_calls ({}) VALUES ({})".format(
    ", ".join(COLUMNS), ", ".join(f"%({c})s" for c in COLUMNS)
)


def row(call, *, step_id: str, run_id: str, seq: int) -> dict:
    """The pipeline_model_calls row for a ModelCall, numbered within its step."""
    return {
        "id": call.id,
        "step_id": step_id,
        "run_id": run_id,
        "seq": seq,
        "started_at": call.started_at,
        "ended_at": max(call.ended_at, call.started_at),
        "provider": call.provider,
        "model": call.model,
        "model_digest": call.model_digest,
        "role": call.role,
        "request": Jsonb(jsonable(call.request)),
        "status": call.status,
        "done_reason": call.done_reason,
        "error_type": call.error_type,
        "input_tokens": call.prompt_tokens,
        "output_tokens": call.completion_tokens,
        "cached_input_tokens": call.cached_input_tokens,
        "reasoning_tokens": call.reasoning_tokens,
        "total_ms": call.total_ms,
        "load_ms": call.load_ms,
        "prompt_eval_ms": call.prompt_eval_ms,
        "eval_ms": call.eval_ms,
        "first_token_ms": call.first_token_ms,
        "wall_ms": call.wall_ms,
        "input_chars": call.input_chars,
        "output_chars": call.output_chars,
        "image_count": call.image_count,
        "image_pixels": Jsonb([list(p) if p else None for p in call.image_pixels]),
        "prompt_truncated": call.prompt_truncated,
    }


def log_fields(call) -> dict:
    """What a log line may say about a call: counts, timings, status; no text."""
    return {
        "event": "model_call_ended",
        "model_call_id": call.id,
        "model": call.model,
        "model_digest": call.model_digest,
        "model_role": call.role,
        "status": call.status,
        "done_reason": call.done_reason,
        "error_type": call.error_type,
        "input_tokens": call.prompt_tokens,
        "output_tokens": call.completion_tokens,
        "cached_input_tokens": call.cached_input_tokens,
        "reasoning_tokens": call.reasoning_tokens,
        "total_ms": call.total_ms,
        "load_ms": call.load_ms,
        "prompt_eval_ms": call.prompt_eval_ms,
        "eval_ms": call.eval_ms,
        "first_token_ms": call.first_token_ms,
        "wall_ms": call.wall_ms,
        "input_chars": call.input_chars,
        "output_chars": call.output_chars,
        "image_count": call.image_count,
        "prompt_truncated": call.prompt_truncated,
    }
