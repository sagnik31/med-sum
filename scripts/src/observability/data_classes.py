"""
The class of every database column and every log field: what each may
hold, and so how it is protected. A test fails when a column or log field
has no class, so a new one can't be added without deciding.

- clinical: patient data, on purpose. Protected by access control and
  deletion with its document, never masked.
- personal: staff and account details that aren't clinical (an e-mail
  address, a session's IP address).
- secret: credentials, stored hashed.
- telemetry: about runs, calls and jobs. Must never hold patient data:
  text written into it is masked (observability.redact), and the leak test
  checks it over the whole dataset.
- safe: ids, timestamps, statuses, counts and the pipeline's own
  constants (prompts, schema hashes).

Logs carry only safe and telemetry fields. See
docs/adr/0008-patient-data-protection.md.
"""

CLINICAL, PERSONAL, SECRET, TELEMETRY, SAFE = (
    "clinical", "personal", "secret", "telemetry", "safe",
)  # fmt: skip
CLASSES = (CLINICAL, PERSONAL, SECRET, TELEMETRY, SAFE)
# What may leave the clinical stores: into logs, reports and traces.
OUTSIDE_CLINICAL = (TELEMETRY, SAFE)


def _cols(cls: str, names: str) -> dict[str, str]:
    return dict.fromkeys(names.split(), cls)


COLUMNS: dict[str, dict[str, str]] = {
    "organizations": _cols(SAFE, "id name created_at"),
    "users": {
        **_cols(
            SAFE,
            "id org_id roles status password_changed_at failed_logins"
            " locked_until created_by created_at last_login_at"
            " patient_insights_status patient_insights_updated_at",
        ),
        **_cols(PERSONAL, "email registration_no"),
        # Until patients are records of their own, the account is the patient.
        **_cols(CLINICAL, "full_name patient_insights"),
        **_cols(SECRET, "password_hash"),
        **_cols(TELEMETRY, "patient_insights_error"),
    },  # fmt: skip
    "sessions": {
        **_cols(SECRET, "token_hash"),
        **_cols(SAFE, "id user_id created_at last_seen_at expires_at"),
        **_cols(PERSONAL, "ip user_agent"),
    },
    "one_time_tokens": {
        **_cols(SECRET, "token_hash"),
        **_cols(SAFE, "user_id purpose created_at expires_at used_at created_by"),
    },
    "documents": {
        **_cols(SAFE, "id user_id content_type storage_path uploaded_at updated_at"),
        # A file name can name the patient; the hash fingerprints the file.
        **_cols(CLINICAL, "original_name extracted_markdown sha256"),
    },
    "insights": {
        **_cols(SAFE, "id document_id user_id status created_at updated_at"),
        **_cols(CLINICAL, "html_insights"),
        **_cols(TELEMETRY, "error_message"),
    },
    "jobs": {
        **_cols(
            SAFE,
            "id document_id status attempts claimed_by lease_until run_id"
            " created_at updated_at",
        ),
        **_cols(TELEMETRY, "error"),
    },  # fmt: skip
    "document_records": {
        **_cols(SAFE, "document_id schema_version run_id created_at updated_at"),
        **_cols(CLINICAL, "record"),
    },
    "pipeline_runs": {
        **_cols(SAFE, "id trace_id document_id"),
        **_cols(
            TELEMETRY,
            "graph_version started_at ended_at end_state models_used"
            " error code_version code_dirty schema_hash db_schema_hash",
        ),
    },  # fmt: skip
    "pipeline_steps": {
        **_cols(SAFE, "id run_id parent_step_id"),
        **_cols(
            TELEMETRY,
            "seq node attempt page started_at ended_at status model"
            " model_role prompt_name prompt_hash prompt_tokens"
            " completion_tokens findings error",
        ),
        # A step's own content: the page's lines, the model's replies. Other
        # stores' content is kept by reference (pipeline.nodes.content_ref).
        **_cols(CLINICAL, "input output"),
    },  # fmt: skip
    "pipeline_model_calls": {
        **_cols(SAFE, "id step_id run_id"),
        **_cols(
            TELEMETRY,
            "seq started_at ended_at provider model model_digest role"
            " request status done_reason error_type input_tokens"
            " output_tokens cached_input_tokens reasoning_tokens"
            " total_ms load_ms prompt_eval_ms eval_ms first_token_ms"
            " wall_ms input_chars output_chars image_count"
            " image_pixels prompt_truncated",
        ),
    },  # fmt: skip
    "prompts": _cols(SAFE, "hash name text first_used_at"),
}

# Schemas classed as a whole: LangGraph's checkpoint tables hold the graph's
# state while a run is open.
SCHEMAS = {"langgraph": CLINICAL}

LOG_FIELDS: dict[str, str] = {
    **_cols(SAFE, "event run_id trace_id document_id step_id span_id parent_span_id"
                  " model_call_id job_id user_id reused_from_step"),
    **_cols(TELEMETRY, "node seq attempt attempts page status end_state duration_ms"
                       " graph_version model model_role model_calls models_used"
                       " model_digest prompt_name prompt_hash prompt_tokens"
                       " completion_tokens findings error_type failures breaker"
                       " cooldown_s quality_kind quality_failures done_reason"
                       " input_tokens output_tokens cached_input_tokens"
                       " reasoning_tokens total_ms load_ms prompt_eval_ms eval_ms"
                       " first_token_ms wall_ms input_chars output_chars image_count"
                       " prompt_truncated fallback_calls capped_calls failed_calls"
                       " model_wall_ms model_total_ms steps_left_running"
                       " steps_interrupted documents markdown_chars html_chars"
                       " not_included awaiting overview unknown_numbers"
                       " unknown_words reprocess result"),
}  # fmt: skip


def columns_of(cls: str) -> list[tuple[str, str]]:
    """Every (table, column) of a class."""
    return [(t, c) for t, cols in COLUMNS.items() for c, k in cols.items() if k == cls]
