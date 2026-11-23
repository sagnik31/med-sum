-- med-sum database schema: the whole of it, in one file.
--
-- The database holds synthetic data only, so the schema is changed in
-- place, with no migrations: change this file, then reset the database
-- (`python -m reset_db --yes` from scripts/src). The test suites build
-- their throwaway databases from this file too. See docs/adr/0008-patient-data-protection.md.
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
-- a one-time setup link. Staff are never patients: patients are records
-- of their own (below), with no account.
CREATE TABLE staff_users (
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
    created_by                  uuid REFERENCES staff_users (id) ON DELETE SET NULL,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    last_login_at               timestamptz,
    CHECK (status <> 'active' OR password_hash IS NOT NULL)
);
CREATE INDEX staff_users_org_id_idx ON staff_users (org_id);

-- Login sessions. The cookie holds 32 random bytes; only their SHA-256 is
-- stored, so these rows can't be used to log in. A session ends after 30
-- minutes without a request or at expires_at (12 hours after login),
-- whichever comes first; the API enforces both.
CREATE TABLE sessions (
    token_hash   bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    id           uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),  -- names a session to its owner
    user_id      uuid NOT NULL REFERENCES staff_users (id) ON DELETE CASCADE,
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
    user_id    uuid NOT NULL REFERENCES staff_users (id) ON DELETE CASCADE,
    purpose    text NOT NULL CHECK (purpose IN ('setup', 'reset')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    created_by uuid REFERENCES staff_users (id) ON DELETE SET NULL,
    CHECK (expires_at > created_at)
);
CREATE INDEX one_time_tokens_user_id_idx ON one_time_tokens (user_id);

-- The audit log: one row per login, logout, lockout, password or staff
-- change, and every read or change of patient data, written by the API
-- (a change in its own transaction, a read before the data is returned).
-- It holds ids, never clinical content: details carries only counts,
-- field names and reasons. Rows are never changed or removed, so the
-- columns have no foreign keys (a purged patient's trail stays) and a
-- trigger refuses UPDATE and DELETE. See docs/adr/0010-staff-access-and-security.md.
CREATE TABLE audit_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    org_id      uuid,           -- none for a login with an unknown email
    actor_id    uuid,           -- the staff account; none when unknown
    action      text NOT NULL CHECK (action ~ '^[a-z_]+$'),
    target_type text,
    target_id   text,
    patient_id  uuid,
    outcome     text NOT NULL CHECK (outcome IN ('success', 'failure', 'denied')),
    ip          inet,
    user_agent  text,
    details     jsonb CHECK (details IS NULL OR jsonb_typeof(details) = 'object')
);
CREATE INDEX audit_events_org_idx ON audit_events (org_id, id DESC);
CREATE INDEX audit_events_actor_idx ON audit_events (actor_id, id DESC);
CREATE INDEX audit_events_patient_idx ON audit_events (patient_id, id DESC);

CREATE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % refused', TG_OP;
END;
$$;

CREATE TRIGGER audit_events_no_update_or_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

-- -------------------------------------------------------------------
-- Patients: records, not accounts. A clinician creates one and uploads its
-- documents; every clinician of the organisation sees every patient of
-- it. The MRN (the hospital's patient ID) is required and unique within
-- the organisation, and is what the patient-match check compares a
-- document's printed ID with. A date of birth, or an age when that is all
-- that is known, never both. See docs/adr/0010-staff-access-and-security.md.
-- -------------------------------------------------------------------
CREATE TABLE patients (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations (id),
    mrn           text NOT NULL CHECK (mrn <> '' AND mrn = btrim(mrn)),
    full_name     text NOT NULL CHECK (full_name <> ''),
    date_of_birth date,
    age_years     integer CHECK (age_years BETWEEN 0 AND 130),
    sex           text CHECK (sex IN ('F', 'M', 'O')),
    created_by    uuid REFERENCES staff_users (id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, mrn),
    CHECK (date_of_birth IS NULL OR age_years IS NULL)
);
CREATE INDEX patients_org_id_name_idx ON patients (org_id, lower(full_name));

-- A patient's cumulative summary, built by the AI service from the
-- patient's document records. No row until one is first asked for.
-- status 'processing' while a run holds the claim; the run's result and
-- failure are written only while updated_at still equals the claim it set.
-- A request that arrives while a run holds the claim sets rerun_requested:
-- that run read its inputs before the request (a review or a delete may
-- have changed them), so when it ends it claims the summary again and
-- builds it once more instead of finishing.
CREATE TABLE patient_summaries (
    patient_id      uuid PRIMARY KEY REFERENCES patients (id) ON DELETE CASCADE,
    html            text,            -- the summary page, kept while a new one is built
    status          text NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    error           text,            -- masked; set when status is failed
    rerun_requested boolean NOT NULL DEFAULT false,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- -------------------------------------------------------------------
-- Documents and their processing
-- -------------------------------------------------------------------
-- A document belongs to a patient and records who uploaded it. Deleting
-- one is soft (deleted_at, deleted_by): the row and its file stay, and
-- every query that shows or uses documents leaves deleted ones out.
CREATE TABLE documents (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id         uuid NOT NULL REFERENCES patients (id) ON DELETE CASCADE,
    uploaded_by        uuid REFERENCES staff_users (id) ON DELETE SET NULL,  -- null for rows made without an upload
    original_name      text NOT NULL,
    content_type       text NOT NULL,
    storage_path       text NOT NULL,          -- absolute; never sent to clients
    uploaded_at        timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    extracted_markdown text,                   -- the latest run's transcription, as plain text
    -- SHA-256 of the uploaded file. The same file uploaded again for the
    -- same patient is flagged as a duplicate of the first (not refused),
    -- and the patient summary counts it once. Null for rows made without
    -- an upload. See docs/adr/0008-patient-data-protection.md.
    sha256             text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    deleted_at         timestamptz,
    deleted_by         uuid REFERENCES staff_users (id) ON DELETE SET NULL,
    CHECK (deleted_by IS NULL OR deleted_at IS NOT NULL)
);
CREATE INDEX documents_patient_id_idx ON documents (patient_id, uploaded_at);
CREATE INDEX documents_patient_id_sha256_idx ON documents (patient_id, sha256, uploaded_at);

-- Where a document's processing stands: at most one row per document, and
-- its status is what the API reports; no row means not started. processing
-- while a job is queued or running (see jobs), then the run's end state.
-- No run ends verified: a checks_passed document becomes verified only
-- when a clinician signs it off (or finishes its audit), and a
-- needs_review one when every flagged value was reviewed. See
-- docs/adr/0013-model-choice-after-the-first-live-evals.md.
CREATE TABLE insights (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL UNIQUE REFERENCES documents (id) ON DELETE CASCADE,
    status        text NOT NULL DEFAULT 'processing'
                  CHECK (status IN ('processing', 'checks_passed', 'verified',
                                    'needs_review', 'failed')),
    html_insights text,
    error_message text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX insights_status_idx ON insights (status);

-- -------------------------------------------------------------------
-- Pipeline runs and steps: what produced what. See docs/adr/0006-durable-traceable-runs.md.
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
    end_state     text CHECK (end_state IN ('checks_passed', 'needs_review', 'failed')),
    models_used   text[] NOT NULL DEFAULT '{}',
    error         text,
    -- What the run was made with: the git commit (null when unknown);
    -- whether the tree had changes (null when not known, e.g. a commit
    -- given by MEDSUM_CODE_VERSION); a SHA-256 of the structured-output
    -- schemas the models are sent; and one of the database's own table
    -- structure. See docs/adr/0006-durable-traceable-runs.md.
    code_version   text,
    code_dirty     boolean,
    schema_hash    text,
    db_schema_hash text,
    CHECK ((end_state IS NULL) = (ended_at IS NULL)),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX pipeline_runs_document_id_idx ON pipeline_runs (document_id, started_at);

-- A run's checkpoints (its LangGraph thread, named by the run's id) are
-- needed only while the run can still resume, and they hold the graph's
-- state, document content included. So they are deleted when the run ends
-- and when the run is deleted (with its document, by ON DELETE CASCADE).
-- The AI service creates the checkpoint tables after this file is loaded,
-- so the function looks each one up when it runs. SECURITY DEFINER: the
-- role deleting a document needn't be able to touch the checkpoint schema.
-- See docs/adr/0008-patient-data-protection.md.
CREATE FUNCTION delete_run_checkpoints() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    thread text := OLD.id::text;
BEGIN
    IF to_regclass('langgraph.checkpoint_writes') IS NOT NULL THEN
        DELETE FROM langgraph.checkpoint_writes WHERE thread_id = thread;
    END IF;
    IF to_regclass('langgraph.checkpoint_blobs') IS NOT NULL THEN
        DELETE FROM langgraph.checkpoint_blobs WHERE thread_id = thread;
    END IF;
    IF to_regclass('langgraph.checkpoints') IS NOT NULL THEN
        DELETE FROM langgraph.checkpoints WHERE thread_id = thread;
    END IF;
    RETURN NULL;
END
$$;
CREATE TRIGGER pipeline_runs_ended_delete_checkpoints
    AFTER UPDATE OF ended_at ON pipeline_runs
    FOR EACH ROW WHEN (OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL)
    EXECUTE FUNCTION delete_run_checkpoints();
CREATE TRIGGER pipeline_runs_deleted_delete_checkpoints
    AFTER DELETE ON pipeline_runs
    FOR EACH ROW EXECUTE FUNCTION delete_run_checkpoints();

-- The text of every prompt a step has used, by the hash that
-- pipeline_steps.prompt_hash records, written the first time a step uses
-- it, so an old step can be read and replayed with the prompt it was sent.
-- Prompts are the pipeline's own instructions, never document content, so
-- rows outlive the documents and runs that used them.
CREATE TABLE prompts (
    hash          text PRIMARY KEY CHECK (hash ~ '^sha256:[0-9a-f]{64}$'),
    name          text NOT NULL,    -- the name it was first used under
    text          text NOT NULL,
    first_used_at timestamptz NOT NULL DEFAULT now()
);

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

-- What the "Processing details" panel shows for a document: each step of
-- its latest run, in order, with its status, model and role, duration,
-- attempt (a repair is attempt 2 or more), model calls, and the validator
-- findings that failed it (error or flag: validator, severity, item and
-- field, never the message, which quotes the document). error is the
-- step's masked error text. runs counts every run of the document. The API
-- reads this view and none of the tables under it.
-- See docs/adr/0006-durable-traceable-runs.md.
CREATE VIEW document_processing_steps AS
SELECT r.document_id,
       r.id                                                   AS run_id,
       r.graph_version,
       r.started_at                                           AS run_started_at,
       r.ended_at                                             AS run_ended_at,
       r.end_state,
       r.models_used,
       (SELECT count(*) FROM pipeline_runs o
        WHERE o.document_id = r.document_id)                  AS runs,
       s.seq,
       s.node,
       s.attempt,
       s.page,
       s.status,
       s.model,
       s.model_role,
       s.started_at,
       s.ended_at,
       (extract(epoch FROM (s.ended_at - s.started_at)) * 1000)::bigint
                                                              AS duration_ms,
       (SELECT count(*) FROM pipeline_model_calls c
        WHERE c.step_id = s.id)                               AS model_calls,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
                     'validator', f ->> 'validator', 'severity', f ->> 'severity',
                     'item_id', f ->> 'item_id', 'field', f ->> 'field'))
                 FROM jsonb_array_elements(s.findings) f
                 WHERE f ->> 'severity' IN ('error', 'flag')), '[]'::jsonb)
                                                              AS failures,
       s.error
FROM pipeline_runs r
JOIN pipeline_steps s ON s.run_id = r.id
WHERE r.id = (SELECT o.id FROM pipeline_runs o
              WHERE o.document_id = r.document_id
              ORDER BY o.started_at DESC, o.id DESC
              LIMIT 1);

-- -------------------------------------------------------------------
-- The trace screen. Two audiences, one source:
--
-- document_trace_runs, document_trace_steps and document_trace_prompts are
-- a document's runs in full, for clinicians of its organisation (the API
-- checks the document and audits every view): each step's input, output,
-- findings with their messages, masked error, prompt and model, and
-- whether `python -m pipeline.replay <step_id>` can rebuild it. They hold
-- document content.
--
-- org_pipeline_runs and org_pipeline_steps are the same runs with no
-- content, for an organisation's admins: timings, models, tokens, statuses,
-- end states, and each failing finding's validator, severity, item and
-- field. No document, patient, input, output, message or error text. Runs
-- of deleted documents are left out of both.
--
-- The API reads these views and none of the tables under them.
-- See docs/adr/0006-durable-traceable-runs.md.
CREATE VIEW document_trace_runs AS
SELECT r.document_id,
       p.org_id,
       r.id                                                   AS run_id,
       r.graph_version,
       r.started_at,
       r.ended_at,
       r.end_state,
       r.models_used,
       r.error,
       r.code_version,
       r.code_dirty,
       r.schema_hash,
       r.db_schema_hash,
       (SELECT count(*) FROM pipeline_steps s WHERE s.run_id = r.id)
                                                              AS steps,
       (SELECT count(*) FROM pipeline_steps s
        WHERE s.run_id = r.id AND s.status = 'failed')        AS failed_steps
FROM pipeline_runs r
JOIN documents d ON d.id = r.document_id
JOIN patients p ON p.id = d.patient_id
WHERE d.deleted_at IS NULL;

-- replayable mirrors what pipeline.replay accepts: a model step (transcribe,
-- classify, parse, repair) that has ended with an output and whose prompt
-- text is stored; a repair only when it ran without tools and stored its
-- problems (scripts/tests/test_trace_views.py keeps the two in step).
CREATE VIEW document_trace_steps AS
SELECT r.document_id,
       p.org_id,
       s.run_id,
       s.id                                                   AS step_id,
       s.parent_step_id,
       s.seq,
       s.node,
       s.attempt,
       s.page,
       s.status,
       s.model,
       s.model_role,
       s.started_at,
       s.ended_at,
       (extract(epoch FROM (s.ended_at - s.started_at)) * 1000)::bigint
                                                              AS duration_ms,
       s.prompt_tokens,
       s.completion_tokens,
       (SELECT count(*) FROM pipeline_model_calls c
        WHERE c.step_id = s.id)                               AS model_calls,
       s.prompt_name,
       s.prompt_hash,
       s.input,
       s.output,
       s.findings,
       s.error,
       (s.node IN ('transcribe', 'classify', 'parse', 'repair')
        AND s.status <> 'running'
        AND s.output IS NOT NULL
        AND EXISTS (SELECT 1 FROM prompts pr WHERE pr.hash = s.prompt_hash)
        AND (s.node <> 'repair'
             OR (s.prompt_name NOT LIKE '%+tools'
                 AND s.input ? 'repairs' AND s.input ? 'system_prompt_hash')))
                                                              AS replayable
FROM pipeline_steps s
JOIN pipeline_runs r ON r.id = s.run_id
JOIN documents d ON d.id = r.document_id
JOIN patients p ON p.id = d.patient_id
WHERE d.deleted_at IS NULL;

-- The text of every prompt a document's steps used, once per hash.
CREATE VIEW document_trace_prompts AS
SELECT DISTINCT r.document_id, pr.hash, pr.name, pr.text
FROM prompts pr
JOIN pipeline_steps s ON s.prompt_hash = pr.hash
JOIN pipeline_runs r ON r.id = s.run_id
JOIN documents d ON d.id = r.document_id
WHERE d.deleted_at IS NULL;

CREATE VIEW org_pipeline_runs AS
SELECT org_id, run_id, graph_version, started_at, ended_at, end_state,
       models_used, error IS NOT NULL AS has_error, code_version, code_dirty,
       steps, failed_steps
FROM document_trace_runs;

CREATE VIEW org_pipeline_steps AS
SELECT t.org_id, t.run_id, t.step_id, t.parent_step_id, t.seq, t.node,
       t.attempt, t.page, t.status, t.model, t.model_role, t.started_at,
       t.ended_at, t.duration_ms, t.prompt_tokens, t.completion_tokens,
       t.model_calls, t.prompt_name,
       COALESCE((SELECT jsonb_agg(jsonb_build_object(
                     'validator', f ->> 'validator', 'severity', f ->> 'severity',
                     'item_id', f ->> 'item_id', 'field', f ->> 'field'))
                 FROM jsonb_array_elements(t.findings) f
                 WHERE f ->> 'severity' IN ('error', 'flag')), '[]'::jsonb)
                                                              AS failures,
       t.error IS NOT NULL                                    AS has_error,
       t.replayable
FROM document_trace_steps t;

-- -------------------------------------------------------------------
-- The structured record of a document: one JSONB value per document,
-- with its schema version (see docs/adr/0003-printed-and-derived-values.md). Reprocessing replaces
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

-- One row per clinician review of a record item: a confirmation of its
-- reading, or a correction of one field. The review itself also goes into
-- the record (provenance and corrections, next to what was read); these
-- rows are the trail that outlives a reprocess, and what signal precision
-- is counted from. signals are the item's low-confidence signal ids when
-- it was reviewed (ids only, e.g. LC-04); findings are the names of the
-- value checks a correction failed, which the clinician acknowledged.
-- original and corrected hold the reading and the entered value (clinical
-- data, like the record). See docs/adr/0011-clinician-review.md.
CREATE TABLE record_reviews (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    run_id      uuid REFERENCES pipeline_runs (id) ON DELETE SET NULL,  -- the record's run
    item_id     text NOT NULL CHECK (item_id ~ '^[a-z]+(-[0-9]+)?$'),
    field       text CHECK (field ~ '^[a-z_]+(\[[0-9]+\])?$'),
    action      text NOT NULL CHECK (action IN ('confirm', 'correct')),
    original    text,
    corrected   text,
    note        text,
    signals     text[] NOT NULL DEFAULT '{}',
    findings    text[] NOT NULL DEFAULT '{}',
    audit       boolean NOT NULL DEFAULT false,  -- reviewed as an audit sample
    reviewer_id uuid REFERENCES staff_users (id) ON DELETE SET NULL,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((action = 'correct') = (field IS NOT NULL AND corrected IS NOT NULL))
);
CREATE INDEX record_reviews_document_idx ON record_reviews (document_id, reviewed_at);

-- A share of the documents that passed every check go back to a
-- clinician as an audit, every value checked before it is finished:
-- nothing in them raised a signal, so only a sample checked value by
-- value can tell how many errors get through unflagged. The AI service
-- picks them when a run ends checks_passed, from a hash of the run's id (so a
-- resumed run picks the same way), at MEDSUM_AUDIT_SAMPLE_RATE. A sample
-- is open until a clinician finishes the audit; reviews made while it is
-- open are stored with audit set. A reprocess makes a new run, and the
-- queue shows only a sample of the document's current record.
-- See docs/adr/0011-clinician-review.md.
CREATE TABLE audit_samples (
    run_id       uuid PRIMARY KEY REFERENCES pipeline_runs (id) ON DELETE CASCADE,
    document_id  uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    sampled_at   timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    completed_by uuid REFERENCES staff_users (id) ON DELETE SET NULL,
    CHECK (completed_by IS NULL OR completed_at IS NOT NULL)
);
CREATE INDEX audit_samples_open_idx ON audit_samples (document_id) WHERE completed_at IS NULL;

-- How often each low-confidence signal was right, from review outcomes: an
-- item raised by a signal that the clinician corrected (any field of it)
-- was a true catch; one only confirmed was a false alarm. Each item counts
-- once per run, whatever number of reviews it took. Audit reviews are left
-- out (their items raised no signal). Counts only, no values.
CREATE VIEW review_signal_precision AS
WITH outcomes AS (
    SELECT p.org_id, s.signal, r.document_id, r.run_id, r.item_id,
           bool_or(r.action = 'correct') AS corrected
    FROM record_reviews r
    CROSS JOIN LATERAL unnest(r.signals) AS s (signal)
    JOIN documents d ON d.id = r.document_id
    JOIN patients p ON p.id = d.patient_id
    WHERE NOT r.audit
    GROUP BY p.org_id, s.signal, r.document_id, r.run_id, r.item_id
)
SELECT org_id, signal,
       count(*)                           AS reviewed,
       count(*) FILTER (WHERE corrected)     AS corrected,
       count(*) FILTER (WHERE NOT corrected) AS confirmed
FROM outcomes
GROUP BY org_id, signal;

-- Each audit sample and whether an error got through: a correction made
-- during the audit means the document held a wrong value that no
-- signal raised. finished is false while the audit is open. Only reviews
-- of the sample's run count: a reprocessed document's later audit is of
-- another run and has its own sample.
CREATE VIEW audit_sample_outcomes AS
SELECT p.org_id, s.document_id, s.run_id, s.sampled_at, s.completed_at,
       s.completed_at IS NOT NULL AS finished,
       EXISTS (SELECT 1 FROM record_reviews r
               WHERE r.document_id = s.document_id AND r.run_id = s.run_id AND r.audit
                 AND r.action = 'correct' AND r.reviewed_at >= s.sampled_at
                 AND (s.completed_at IS NULL OR r.reviewed_at <= s.completed_at)) AS escaped
FROM audit_samples s
JOIN documents d ON d.id = s.document_id
JOIN patients p ON p.id = d.patient_id;

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
