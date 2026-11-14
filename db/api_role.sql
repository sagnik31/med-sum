-- The Go API's own Postgres role: medsum_api, with the rows the API's
-- queries need and nothing else, so a leaked DATABASE_URL can't alter or
-- drop a table, rewrite the audit log, hard-delete patient data or read
-- the AI service's pipeline tables.
--
-- Run it as a superuser (or the schema's owner, with CREATEROLE) on the
-- API's database, after db/schema.sql:
--
--     psql -U postgres -d med_sum -f db/api_role.sql
--     psql -U postgres -d med_sum -c "\password medsum_api"
--
-- It creates the role without a password (it can't log in until one is
-- set) and grants its privileges. It is safe to run again; python -m
-- reset_db runs it after reloading the schema whenever the role exists,
-- since dropping the tables drops their grants. medsumctl and reset_db
-- keep connecting as the schema's owner.
--
-- What the API does with each table, and so what it is granted:
--   organizations      reads them (medsumctl creates them)
--   staff_users        reads, creates and changes accounts (logins, staff
--                      management, passwords); never deletes one
--   sessions           reads, creates, touches and deletes sessions
--   one_time_tokens    reads, creates, uses and replaces setup and reset links
--   audit_events       adds rows and reads them; never changes or deletes one
--   patients           reads, creates and changes patients; never deletes one
--   documents          reads and uploads them, and deletes softly
--                      (deleted_at, deleted_by); never deletes a row
--   insights, document_records  reads them (the AI service writes them);
--                      stores a clinician's review: the reviewed record, the
--                      page built from it and the verified status
--   record_reviews     adds review rows and reads them; never changes one
--   document_processing_steps  reads it (the latest run's steps, with no
--                      document content); none of the pipeline tables under it
--   patient_summaries  reads them (the AI service writes them)
-- Nothing on jobs, prompts, the pipeline tables and their other views, or the
-- langgraph schema. Keep this list in step with the API's queries;
-- backend/internal/db/role_test.go runs them as this role.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medsum_api') THEN
        CREATE ROLE medsum_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO medsum_api', current_database());
END
$$;

-- Earlier grants are revoked first, so a privilege removed here is removed
-- from the database too when this runs again.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM medsum_api;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM medsum_api;
GRANT USAGE ON SCHEMA public TO medsum_api;

GRANT SELECT ON organizations TO medsum_api;

GRANT SELECT, INSERT ON staff_users TO medsum_api;
GRANT UPDATE (full_name, registration_no, roles, status, password_hash, password_changed_at,
              failed_logins, locked_until, last_login_at)
    ON staff_users TO medsum_api;

GRANT SELECT, INSERT, DELETE ON sessions TO medsum_api;
GRANT UPDATE (last_seen_at) ON sessions TO medsum_api;

GRANT SELECT, INSERT, DELETE ON one_time_tokens TO medsum_api;
GRANT UPDATE (used_at) ON one_time_tokens TO medsum_api;

GRANT SELECT, INSERT ON audit_events TO medsum_api;

GRANT SELECT, INSERT ON patients TO medsum_api;
GRANT UPDATE (mrn, full_name, date_of_birth, age_years, sex, updated_at) ON patients TO medsum_api;

GRANT SELECT, INSERT ON documents TO medsum_api;
GRANT UPDATE (deleted_at, deleted_by, updated_at) ON documents TO medsum_api;

GRANT SELECT ON insights, document_records, patient_summaries TO medsum_api;
GRANT UPDATE (html_insights, status, updated_at) ON insights TO medsum_api;
GRANT UPDATE (record, updated_at) ON document_records TO medsum_api;

GRANT SELECT, INSERT ON record_reviews TO medsum_api;

GRANT SELECT ON document_processing_steps TO medsum_api;
