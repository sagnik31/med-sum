-- The AI service's own Postgres role: medsum_ai, with only what the service
-- needs, so a leaked DATABASE_URL can't read accounts, sessions or password
-- hashes, or drop or alter a table.
--
-- Run it as a superuser (or the schema's owner, with CREATEROLE) on the
-- service's database, after db/schema.sql:
--
--     psql -U postgres -d med_sum -f db/ai_service_role.sql
--     psql -U postgres -d med_sum -c "\password medsum_ai"
--
-- It creates the role without a password (it can't log in until one is
-- set) and grants its privileges. It is safe to run again; python -m
-- reset_db runs it after reloading the schema whenever the role exists,
-- since dropping the tables drops their grants.
--
-- What the service does with each table, and so what it is granted:
--   documents          reads them; writes the transcription (extracted_markdown)
--   patients           reads the details the patient-match check compares a
--                      document with (name, sex, date of birth or age, MRN);
--                      never writes them
--   patient_summaries  reads and writes (claims, results and failures)
--   staff_users        reads id and full_name (who uploaded a document, kept
--                      on its record); nothing else of an account
--   insights, jobs, document_records, pipeline_runs, pipeline_steps,
--   pipeline_model_calls  reads and writes (no deletes: rows go with their
--                      document, through ON DELETE CASCADE)
--   prompts            reads and adds (a prompt's text is written once, on
--                      first use, and never changed)
--   pipeline_* views   reads
--   audit_samples      adds them (a share of verified runs, sampled for audit);
--                      never reads or changes one
--   review_signal_precision, audit_sample_outcomes  reads (counts and
--                      ids for the review report; record_reviews itself,
--                      which holds clinical values, is not granted)
--   langgraph schema   creates and migrates its checkpoint tables, and reads,
--                      writes and deletes checkpoints
-- Nothing on organizations, sessions, one_time_tokens, audit_events or
-- record_reviews.
-- Keep this list in step with the service's queries; tests/test_ai_service_role.py
-- runs the service's database work as this role.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsum_ai') THEN
        CREATE ROLE medsum_ai LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO medsum_ai', current_database());
END
$$;

-- Earlier grants are revoked first, so a privilege removed here is removed
-- from the database too when this runs again.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM medsum_ai;
GRANT USAGE ON SCHEMA public TO medsum_ai;

GRANT SELECT ON documents TO medsum_ai;
GRANT UPDATE (extracted_markdown, updated_at) ON documents TO medsum_ai;

GRANT SELECT (id, full_name, sex, date_of_birth, age_years, mrn) ON patients TO medsum_ai;
GRANT SELECT, INSERT, UPDATE ON patient_summaries TO medsum_ai;
GRANT SELECT (id, full_name) ON staff_users TO medsum_ai;

GRANT SELECT, INSERT, UPDATE
    ON insights, jobs, document_records,
       pipeline_runs, pipeline_steps, pipeline_model_calls
    TO medsum_ai;

GRANT SELECT, INSERT ON prompts TO medsum_ai;
GRANT INSERT ON audit_samples TO medsum_ai;
GRANT SELECT ON review_signal_precision, audit_sample_outcomes TO medsum_ai;

GRANT SELECT
    ON pipeline_run_model_totals, pipeline_document_model_totals,
       pipeline_node_model_baseline, pipeline_model_baseline
    TO medsum_ai;

-- Checkpoints: the service creates and migrates these tables itself
-- (pipeline.checkpoints.setup), so it may create in this schema; tables the
-- owner created (python -m reset_db) are granted to it here and by default.
GRANT USAGE, CREATE ON SCHEMA langgraph TO medsum_ai;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA langgraph TO medsum_ai;
ALTER DEFAULT PRIVILEGES IN SCHEMA langgraph
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO medsum_ai;
