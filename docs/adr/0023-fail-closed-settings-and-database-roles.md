# 0023. Database settings fail closed; each service gets a least-privilege role

- **Status:** Accepted, 2026-11-13. The settings module and the AI service's role are built; the Go backend's role is not built yet.

## Context

Five modules of the AI service each defaulted a missing `DATABASE_URL` to `postgres:postgres@localhost`, a superuser. A deployment that forgot the variable would silently connect to a local database with every privilege. And the AI service connected as the schema's owner, so a bug or an injected instruction reaching its queries could read password hashes or drop tables.

## Decision

- **One settings module** (`scripts/src/settings.py`) reads `DATABASE_URL` and the eval runner's admin URL. No other module reads them from the environment.
- **Fail closed:** only `MEDSUM_ENV=dev` may fall back to the local development URLs, which exist only in that module (a test enforces it). Any other value, including no `MEDSUM_ENV` at all, makes a missing URL an error, raised when the service starts.
- **The AI service's role**, `medsum_ai` (`db/ai_service_role.sql`), gets only what it uses: read documents and write their transcription; read and write processing state (insights, jobs, records, runs, steps, model calls); read four views; its checkpoint schema; and only the account columns the patient summary needs. Nothing on organisations, sessions or setup tokens, no DELETE or TRUNCATE in `public` (rows go by cascade), no DDL there. The role file is separate from the schema because roles are server-wide; `reset_db` re-grants it.
- **The Go backend's role** is to be its own least-privilege role too: broader than the AI service's (it deletes documents and sessions), with no DDL. The admin tools (`reset_db`, `medsumctl`) keep the owner.

## Alternatives rejected

- **Defaults everywhere, documented.** Fails open, which is the bug.
- **One shared application role.** The AI service has no reason to see emails, password hashes or sessions.
- **Row-level security.** Useful once patient data is scoped by organisation; column grants already cover what the AI service must not see.

## Consequences

- Development keeps working with no configuration (`MEDSUM_ENV=dev` is set by the dev run script).
- A test runs a document job and a patient summary as a throwaway copy of the role and checks what it is refused.
- A query the AI service newly needs must be granted in the role file, or it fails in deployment, never silently.

## What would change it

A secrets manager or a platform that injects credentials, which would replace the environment variables but not the fail-closed rule.
