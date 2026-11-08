-- med-sum database schema: the whole of it, in one file.
--
-- The database holds synthetic data only (decision 16), so the schema is
-- changed in place, with no migrations: change this file, then reset the
-- database (`python -m reset_db --yes` from scripts/src). The test suites
-- build their throwaway databases from this file too.
--
-- Plain SQL with no psql commands, so it loads through psql or a driver.
-- Needs PostgreSQL 13+ (gen_random_uuid() is built in).

-- -------------------------------------------------------------------
-- Users: until Phase B, each user is also the patient whose documents
-- they upload. The cumulative patient summary lives on this row.
-- -------------------------------------------------------------------
CREATE TABLE users (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number                text NOT NULL UNIQUE,
    full_name                   text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    last_login_at               timestamptz,
    patient_insights            text,          -- the summary's HTML
    patient_insights_status     text,          -- processing / completed / failed
    patient_insights_error      text,
    patient_insights_updated_at timestamptz
);

-- -------------------------------------------------------------------
-- Documents and their processing
-- -------------------------------------------------------------------
CREATE TABLE documents (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    original_name      text NOT NULL,
    content_type       text NOT NULL,
    storage_path       text NOT NULL,          -- absolute; never sent to clients
    uploaded_at        timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    extracted_markdown text                    -- cached page transcriptions
);
CREATE INDEX documents_user_id_idx ON documents (user_id);

-- The processing job of a document: at most one row per document. Its
-- status is what the API reports; no row means not started. updated_at is
-- the running job's heartbeat.
CREATE TABLE insights (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL UNIQUE REFERENCES documents (id) ON DELETE CASCADE,
    user_id       uuid,
    status        text NOT NULL DEFAULT 'pending',
    html_insights text,
    error_message text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX insights_status_idx ON insights (status);

-- -------------------------------------------------------------------
-- Pipeline runs and steps (section 8.4 of the plan): what produced what.
-- Field names follow OpenTelemetry (trace, span, parent span), so a real
-- exporter can be added later. Inputs and outputs hold patient content:
-- they stay here, never in logs.
-- -------------------------------------------------------------------
CREATE TABLE pipeline_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id      text NOT NULL UNIQUE DEFAULT replace(gen_random_uuid()::text, '-', ''),
    document_id   uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    graph_version text NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    ended_at      timestamptz,
    -- Null while the run is going; then one of the pipeline's end states.
    end_state     text CHECK (end_state IN ('verified', 'needs_review', 'failed')),
    models_used   text[] NOT NULL DEFAULT '{}',
    error         text,
    CHECK ((end_state IS NULL) = (ended_at IS NULL)),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX pipeline_runs_document_id_idx ON pipeline_runs (document_id, started_at);

-- One row per node attempt (a span). A repair is a new attempt of parse.
CREATE TABLE pipeline_steps (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),  -- the span id
    run_id            uuid NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
    parent_step_id    uuid REFERENCES pipeline_steps (id) ON DELETE CASCADE,
    seq               integer NOT NULL CHECK (seq >= 1),     -- order within the run
    node              text NOT NULL,                         -- classify, transcribe, parse...
    attempt           integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    page              integer CHECK (page >= 1),             -- for per-page nodes
    started_at        timestamptz NOT NULL DEFAULT now(),
    ended_at          timestamptz,
    status            text NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'ok', 'failed')),
    model             text,
    model_role        text CHECK (model_role IN ('primary', 'fallback')),
    prompt_name       text,
    prompt_hash       text,
    input             jsonb,
    output            jsonb,
    prompt_tokens     integer CHECK (prompt_tokens >= 0),
    completion_tokens integer CHECK (completion_tokens >= 0),
    findings          jsonb NOT NULL DEFAULT '[]',          -- validator findings
    error             text,
    UNIQUE (run_id, seq),
    CHECK ((status = 'running') = (ended_at IS NULL)),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (model_role IS NULL OR model IS NOT NULL)
);
CREATE INDEX pipeline_steps_run_id_idx ON pipeline_steps (run_id, seq);

-- -------------------------------------------------------------------
-- The structured record of a document (section 8.1): one JSONB value per
-- document, with its schema version (decision 15). Reprocessing replaces
-- it; earlier runs' outputs stay in pipeline_steps. Clinician corrections
-- are inside the record, next to the values they correct.
-- -------------------------------------------------------------------
CREATE TABLE document_records (
    document_id    uuid PRIMARY KEY REFERENCES documents (id) ON DELETE CASCADE,
    schema_version integer NOT NULL CHECK (schema_version >= 1),
    record         jsonb NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    run_id         uuid REFERENCES pipeline_runs (id) ON DELETE SET NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CHECK ((record ->> 'schema_version')::integer = schema_version)
);
