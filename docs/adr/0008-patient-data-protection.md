# 0008. Patient data: protected where it must live, masked where it leaves, deleted with its document

- **Status:** Accepted, 2026-11-07 to 2026-11-13. Built, except the Go backend's own database role. Where each kind of data lives is mapped on the [data-flow page](../data-and-threats.md).

## Context

The product exists to show a clinician the patient's name, values and medicines, so the clinical tables hold patient data on purpose; masking them would break the app. But patient data had been reaching places it has no business in: validator findings and exception messages stored on telemetry rows, steps keeping second copies of records, checkpoints outliving their deleted documents. Five modules defaulted a missing database URL to a local superuser, and the AI service connected as the schema's owner.

## Decision

- **Synthetic data only.** The repository's dataset is fictional and real patient data must not be loaded. That is also why the schema is one hand-written file (`db/schema.sql`) with no migrations: a change edits the file and the development database is reset (`reset_db`, local databases only, listing what it would delete unless given `--yes`).
- **Every column and log field has a data class:** clinical, personal, secret, telemetry or safe (`observability/data_classes.py`). A test fails when one has none, and log fields may only be telemetry or safe.
- **Clinical stores are protected, not masked:** access control ([0010](0010-staff-access-and-security.md)), deletion with the document, least-privilege roles.
- **Telemetry is masked where it is written** (`observability/redact.py`): first the identifiers the run has seen (the expected patient, the printed name, ID and date of birth, and each part of a name), then patterns with a fixed shape (e-mail, Indian mobile, ABHA, Aadhaar, PAN, hospital and lab IDs). Applied to step findings and errors, run, job and summary errors, and by pattern to every log message.
- **Steps don't copy other stores' content.** Validate and render store a SHA-256 and size where they stored the record and page; transcribe and parse keep their output, the only copy of what the model produced in that run.
- **Data goes with its document.** Two triggers on `pipeline_runs` delete a run's checkpoints when the run ends and when it is deleted (as it is with its document), through one `SECURITY DEFINER` function; the worker also deletes the thread once the graph stops. Every upload's SHA-256 is stored; a second upload of the same file for the same patient is accepted and named as a duplicate, and the summary counts it once.
- **Settings fail closed.** One module (`scripts/src/settings.py`) reads the database URLs; only `MEDSUM_ENV=dev` may fall back to the local development URLs, and anything else, including no `MEDSUM_ENV`, fails at start-up.
- **Least-privilege roles.** The AI service connects as `medsum_ai` (`db/ai_service_role.sql`): it reads documents and writes their transcription, reads and writes processing state and its checkpoints, reads only the account columns the summary needs, and can't see emails, password hashes, sessions or setup tokens, delete application rows or alter tables. The Go backend is to get its own role, broader but with no DDL; the admin tools keep the owner.
- **Cloud models** get synthetic text only, never page images ([0005](0005-local-models-with-fallback.md)).

## Alternatives rejected

- **Redact before the database.** The app's purpose is to show this data to the clinician.
- **A named-entity model for names in free text.** Weak on Indian names, and the run already knows the names that matter.
- **Periodic checkpoint clean-up.** Content would outlive its document for the interval; a trigger deletes it in the same transaction.
- **Refusing duplicate uploads.** A second copy can be deliberate; a flag leaves the choice with the user.
- **Defaults with documentation, or one shared application role.** The first fails open; the second gives the AI service what it never needs.
- **A migration tool.** A migration per change for data nobody needs to keep.

## Consequences

- A leak test runs every dataset document and a wrong-patient upload through the graph and finds no dataset name, ID or date of birth in any log line or telemetry column; a test deletes a document and finds no row naming it in any schema, checkpoint blobs included.
- A name the run never saw, written in free text, isn't masked. Telemetry rows can be shown or shared without exposing a patient.
- Every schema change resets the development database. Before any real deployment, migrations must come in, with the schema file as their baseline.
- A new column means choosing its class; a new query by the AI service means a grant.

## What would change it

Real patient data, a cloud provider or free-text notes (pseudonymised identifiers, with the mapping kept apart); encryption at rest and the audit log for a deployment; a database whose data must outlive a schema change.
