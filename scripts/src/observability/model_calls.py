"""
pipeline_model_calls: one row per model round trip made by llm.chat, a
child span of its pipeline step.

Column names follow this project's style. OTEL_GENAI_ATTRIBUTES maps the
OpenTelemetry GenAI semantic conventions onto them, so an OTLP exporter
later is a straight mapping: each value is a SQL expression over one row.
"""

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
