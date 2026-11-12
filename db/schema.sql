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
-- Organisations and staff accounts.
-- One organisation in the MVP; every account belongs to one.
-- -------------------------------------------------------------------
CREATE TABLE organizations (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL UNIQUE CHECK (name <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Staff accounts: clinicians and admins, created by an admin (or by
-- medsumctl), never by sign-up. An account sets its own password through
-- a one-time setup link. Until patients become records of their own,
-- each account is also the patient whose documents it uploads,
-- and the cumulative patient summary lives on this row.
CREATE TABLE users (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                      uuid NOT NULL REFERENCES organizations (id),
    email                       text NOT NULL UNIQUE
                                CHECK (email = lower(email) AND email LIKE '_%@_%'),
    full_name                   text NOT NULL CHECK (full_name <> ''),
    registration_no             text,          -- licence number as entered; not verified
    roles                       text[] NOT NULL DEFAULT '{clinician}'
                                CHECK (cardinality(roles) >= 1
                                       AND roles <@ ARRAY['clinician', 'admin']),
    status                      text NOT NULL DEFAULT 'invited'
                                CHECK (status IN ('invited', 'active', 'disabled')),
    password_hash               text,          -- argon2id, PHC format; null until setup
    password_changed_at         timestamptz,
    failed_logins               integer NOT NULL DEFAULT 0 CHECK (failed_logins >= 0),
    locked_until                timestamptz,
    created_by                  uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    last_login_at               timestamptz,
    patient_insights            text,          -- the summary's HTML
    patient_insights_status     text,          -- processing / completed / failed
    patient_insights_error      text,
    patient_insights_updated_at timestamptz,
    CHECK (status <> 'active' OR password_hash IS NOT NULL)
);
CREATE INDEX users_org_id_idx ON users (org_id);

-- Login sessions. The cookie holds 32 random bytes; only their SHA-256 is
-- stored, so these rows can't be used to log in. A session ends after 30
-- minutes without a request or at expires_at (12 hours after login),
-- whichever comes first; the API enforces both.
CREATE TABLE sessions (
    token_hash   bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    id           uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),  -- names a session to its owner
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    ip           text,
    user_agent   text,
    CHECK (expires_at > created_at),
    CHECK (last_seen_at >= created_at)
);
CREATE INDEX sessions_user_id_idx ON sessions (user_id);
CREATE INDEX sessions_expires_at_idx ON sessions (expires_at);

-- Setup and reset links: single use, stored hashed like sessions. Issuing
-- a new link for an account removes its unused ones.
CREATE TABLE one_time_tokens (
    token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    purpose    text NOT NULL CHECK (purpose IN ('setup', 'reset')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    created_by uuid REFERENCES users (id) ON DELETE SET NULL,
    CHECK (expires_at > created_at)
);
CREATE INDEX one_time_tokens_user_id_idx ON one_time_tokens (user_id);

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
    extracted_markdown text                    -- the latest run's transcription, as plain text
);
CREATE INDEX documents_user_id_idx ON documents (user_id);

-- Where a document's processing stands: at most one row per document, and
-- its status is what the API reports; no row means not started. processing
-- while a job is queued or running (see jobs), then the run's end state.
CREATE TABLE insights (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL UNIQUE REFERENCES documents (id) ON DELETE CASCADE,
    user_id       uuid,
    status        text NOT NULL DEFAULT 'processing'
                  CHECK (status IN ('processing', 'verified', 'needs_review', 'failed')),
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
-- LangGraph checkpoints (the graph's state after each node, so a run
-- that crashed resumes from its last finished node) live in a schema of
-- their own. langgraph-checkpoint-postgres creates and migrates its tables
-- there itself (pipeline.checkpoints.setup() in the AI service); this file
-- only creates the schema. Checkpoints hold document content, like
-- pipeline_steps.
CREATE SCHEMA IF NOT EXISTS langgraph;

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
    UNIQUE (id, run_id),  -- lets a model call name its step and run together
    CHECK ((status = 'running') = (ended_at IS NULL)),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (model_role IS NULL OR model IS NOT NULL)
);
CREATE INDEX pipeline_steps_run_id_idx ON pipeline_steps (run_id, seq);

-- One row per model round trip made by llm.chat (a child span of its
-- step): the primary, the fallback, and calls that raised. A step's
-- prompt_tokens and completion_tokens are the sums of its rows'
-- input_tokens and output_tokens. Never prompt text, reply text or images:
-- sizes and counts only, so a cloud model's cost can be estimated from
-- local runs (token counts don't carry over between tokenizers; characters
-- and pixels do).
--
-- OpenTelemetry GenAI semantic conventions, for an OTLP exporter later
-- (observability.model_calls.OTEL_GENAI_ATTRIBUTES holds the same map):
--   span id                          id (parent span: step_id)
--   gen_ai.operation.name            'chat'
--   gen_ai.provider.name             provider
--   gen_ai.request.model             model
--   gen_ai.response.model            model (Ollama answers with the model asked)
--   gen_ai.request.temperature       request ->> 'temperature'
--   gen_ai.request.max_tokens        request ->> 'num_predict'
--   gen_ai.response.finish_reasons   ARRAY[done_reason]
--   gen_ai.usage.input_tokens        input_tokens
--   gen_ai.usage.output_tokens       output_tokens
--   error.type                       error_type
CREATE TABLE pipeline_model_calls (
    id                  uuid PRIMARY KEY,                -- the span id, made by llm.chat
    step_id             uuid NOT NULL,
    run_id              uuid NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
    seq                 integer NOT NULL CHECK (seq >= 1),  -- order within the step
    started_at          timestamptz NOT NULL,
    ended_at            timestamptz NOT NULL,
    provider            text NOT NULL DEFAULT 'ollama',
    model               text NOT NULL,
    model_digest        text,                            -- null when the lookup failed
    role                text NOT NULL CHECK (role IN ('primary', 'fallback')),
    -- The options sent (temperature, num_ctx, num_predict, think, stop,
    -- structured_output on/off); never the prompt or the schema itself.
    request             jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(request) = 'object'),
    -- ok: ended on its own; capped: hit num_predict; stopped: a call with
    -- a stop marker that ended normally; failed: raised (error_type set).
    status              text NOT NULL CHECK (status IN ('ok', 'capped', 'stopped', 'failed')),
    done_reason         text,                            -- as the provider sent it
    error_type          text,                            -- exception type name only
    input_tokens        integer CHECK (input_tokens >= 0),
    output_tokens       integer CHECK (output_tokens >= 0),
    cached_input_tokens integer CHECK (cached_input_tokens >= 0),  -- null for Ollama
    reasoning_tokens    integer CHECK (reasoning_tokens >= 0),     -- null for Ollama
    total_ms            integer CHECK (total_ms >= 0),   -- the provider's timings
    load_ms             integer CHECK (load_ms >= 0),
    prompt_eval_ms      integer CHECK (prompt_eval_ms >= 0),
    eval_ms             integer CHECK (eval_ms >= 0),
    first_token_ms      integer CHECK (first_token_ms >= 0),  -- measured by llm.chat
    wall_ms             integer NOT NULL CHECK (wall_ms >= 0), -- measured by llm.chat
    input_chars         integer NOT NULL CHECK (input_chars >= 0),   -- message text
    output_chars        integer NOT NULL CHECK (output_chars >= 0),  -- streamed text
    image_count         integer NOT NULL DEFAULT 0 CHECK (image_count >= 0),
    -- [width, height] per image, null where the format wasn't recognised.
    image_pixels        jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(image_pixels) = 'array'),
    -- Heuristic, null without a token count: the prompt left less room
    -- than num_predict in num_ctx. Ollama is believed to cut an over-long
    -- prompt and say so only in its own log.
    prompt_truncated    boolean,
    FOREIGN KEY (step_id, run_id) REFERENCES pipeline_steps (id, run_id) ON DELETE CASCADE,
    UNIQUE (step_id, seq),
    CHECK (ended_at >= started_at),
    CHECK ((status = 'failed') = (error_type IS NOT NULL)),
    CHECK (status <> 'failed' OR (input_tokens IS NULL AND output_tokens IS NULL))
);
CREATE INDEX pipeline_model_calls_run_id_idx ON pipeline_model_calls (run_id);
CREATE INDEX pipeline_model_calls_model_idx ON pipeline_model_calls (model, role);

-- -------------------------------------------------------------------
-- Model-call totals and baselines, derived from pipeline_model_calls.
-- Views, not stored columns, so they can't disagree with the rows; store
-- them only if these get slow. A token sum is null when no call in the
-- group reported that count (cached and reasoning tokens, for Ollama).
-- model_wall_ms is our own measure and includes calls that failed;
-- model_total_ms is the provider's.
-- -------------------------------------------------------------------
CREATE VIEW pipeline_run_model_totals AS
SELECT r.id                                                   AS run_id,
       r.document_id,
       r.started_at,
       r.ended_at,
       r.end_state,
       count(c.id)                                            AS model_calls,
       count(c.id) FILTER (WHERE c.role = 'fallback')         AS fallback_calls,
       count(c.id) FILTER (WHERE c.status = 'capped')         AS capped_calls,
       count(c.id) FILTER (WHERE c.status = 'failed')         AS failed_calls,
       sum(c.input_tokens)                                    AS input_tokens,
       sum(c.output_tokens)                                   AS output_tokens,
       sum(c.cached_input_tokens)                             AS cached_input_tokens,
       sum(c.reasoning_tokens)                                AS reasoning_tokens,
       coalesce(sum(c.wall_ms), 0)                            AS model_wall_ms,
       sum(c.total_ms)                                        AS model_total_ms
FROM pipeline_runs r
LEFT JOIN pipeline_model_calls c ON c.run_id = r.id
GROUP BY r.id;

-- Per document, over all its runs (each reprocess adds a run).
CREATE VIEW pipeline_document_model_totals AS
SELECT t.document_id,
       count(*)                                               AS runs,
       max(t.started_at)                                      AS last_run_started_at,
       sum(t.model_calls)                                     AS model_calls,
       sum(t.fallback_calls)                                  AS fallback_calls,
       sum(t.capped_calls)                                    AS capped_calls,
       sum(t.failed_calls)                                    AS failed_calls,
       sum(t.input_tokens)                                    AS input_tokens,
       sum(t.output_tokens)                                   AS output_tokens,
       sum(t.cached_input_tokens)                             AS cached_input_tokens,
       sum(t.reasoning_tokens)                                AS reasoning_tokens,
       sum(t.model_wall_ms)                                   AS model_wall_ms,
       sum(t.model_total_ms)                                  AS model_total_ms
FROM pipeline_run_model_totals t
GROUP BY t.document_id;

-- Per node and model: what one call costs. Token statistics skip calls
-- without counts; time statistics skip failed calls, whose time is
-- mostly a timeout. Percentiles are interpolated (percentile_cont).
CREATE VIEW pipeline_node_model_baseline AS
SELECT s.node,
       c.model,
       count(*)                                               AS calls,
       count(*) FILTER (WHERE c.status = 'failed')            AS failed_calls,
       avg(c.input_tokens)                                    AS avg_input_tokens,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY c.input_tokens)  AS p50_input_tokens,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY c.input_tokens)  AS p95_input_tokens,
       avg(c.output_tokens)                                   AS avg_output_tokens,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY c.output_tokens) AS p50_output_tokens,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY c.output_tokens) AS p95_output_tokens,
       avg(c.wall_ms) FILTER (WHERE c.status <> 'failed')     AS avg_wall_ms,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY c.wall_ms)
           FILTER (WHERE c.status <> 'failed')                AS p50_wall_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY c.wall_ms)
           FILTER (WHERE c.status <> 'failed')                AS p95_wall_ms,
       avg(c.first_token_ms)                                  AS avg_first_token_ms,
       avg(c.input_chars)                                     AS avg_input_chars,
       avg(c.output_chars) FILTER (WHERE c.status <> 'failed') AS avg_output_chars,
       avg(c.image_count)                                     AS avg_images
FROM pipeline_model_calls c
JOIN pipeline_steps s ON s.id = c.step_id
GROUP BY s.node, c.model;

-- Per model: how often it fails, hits the cap or hands over to the
-- fallback, and how fast it reads and writes. fallback_rate is the share
-- of its calls as primary that the fallback had to follow (the next call
-- in the same step); rates are over all its calls otherwise. Speeds use
-- the provider's own eval timings, over calls that have them.
CREATE VIEW pipeline_model_baseline AS
SELECT c.model,
       count(*)                                               AS calls,
       count(*) FILTER (WHERE c.role = 'primary')             AS primary_calls,
       count(*) FILTER (WHERE c.role = 'fallback')            AS fallback_calls,
       count(*) FILTER (WHERE c.role = 'primary' AND EXISTS (
           SELECT 1 FROM pipeline_model_calls n
           WHERE n.step_id = c.step_id AND n.seq = c.seq + 1
             AND n.role = 'fallback'))                        AS handed_to_fallback,
       round(count(*) FILTER (WHERE c.role = 'primary' AND EXISTS (
           SELECT 1 FROM pipeline_model_calls n
           WHERE n.step_id = c.step_id AND n.seq = c.seq + 1
             AND n.role = 'fallback'))::numeric
             / nullif(count(*) FILTER (WHERE c.role = 'primary'), 0), 4)
                                                              AS fallback_rate,
       round(count(*) FILTER (WHERE c.status = 'capped')::numeric / count(*), 4)
                                                              AS cap_hit_rate,
       round(count(*) FILTER (WHERE c.status = 'failed')::numeric / count(*), 4)
                                                              AS failure_rate,
       round(sum(c.output_tokens) FILTER (WHERE c.eval_ms > 0)::numeric * 1000
             / nullif(sum(c.eval_ms) FILTER (WHERE c.output_tokens IS NOT NULL
                                             AND c.eval_ms > 0), 0), 2)
                                                              AS output_tokens_per_s,
       round(sum(c.input_tokens) FILTER (WHERE c.prompt_eval_ms > 0)::numeric * 1000
             / nullif(sum(c.prompt_eval_ms) FILTER (WHERE c.input_tokens IS NOT NULL
                                                    AND c.prompt_eval_ms > 0), 0), 2)
                                                              AS input_tokens_per_s,
       sum(c.input_tokens)                                    AS input_tokens,
       sum(c.output_tokens)                                   AS output_tokens,
       sum(c.wall_ms)                                         AS wall_ms
FROM pipeline_model_calls c
GROUP BY c.model;

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

-- -------------------------------------------------------------------
-- Document jobs: the queue the AI service's worker takes documents from,
-- one job at a time. A worker claims a job for a lease and renews it while
-- it runs; a job whose lease ran out (the worker died) is claimed again
-- and resumes its run from the last checkpoint. Every write a worker makes
-- for a job checks that it still holds the claim, so a worker that lost
-- it can't overwrite the new owner's result. A job claimed more than the
-- allowed number of times is parked, so a document that kills its worker
-- can't loop.
-- -------------------------------------------------------------------
CREATE TABLE jobs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    status      text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'done', 'failed', 'parked')),
    attempts    integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claimed_by  text,
    lease_until timestamptz,
    -- The pipeline run of the current attempt, resumed after a takeover.
    run_id      uuid REFERENCES pipeline_runs (id) ON DELETE SET NULL,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'running') = (claimed_by IS NOT NULL AND lease_until IS NOT NULL))
);
-- At most one open job per document.
CREATE UNIQUE INDEX jobs_open_document_idx ON jobs (document_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX jobs_next_idx ON jobs (created_at) WHERE status IN ('queued', 'running');
