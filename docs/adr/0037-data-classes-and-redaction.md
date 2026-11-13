# 0037. Every column has a data class; identifiers are masked where data leaves the clinical stores

- **Status:** Accepted, 2026-11-13. Built.

## Context

The product has to show a clinician the patient's name, values and medicines, so the clinical tables hold patient data on purpose. Masking them would break the app. Patient data had also been reaching places it has no business in:
- validator findings quote the text they are about, the patient's name included, and are stored on telemetry rows;
- an exception's message can quote a document, and it was stored as a step's, run's or job's error;
- steps kept second copies of content that other tables already hold (the parse, the record, the rendered page).

Nothing stopped a cloud model being configured and sent a page.

## Decision

- **Every column and log field has one class:** clinical, personal, secret, telemetry or safe (`observability/data_classes.py`). A test fails when a column in the schema, or a field the logger may write, has none. Log fields may only be telemetry or safe.
- **Clinical stores are protected, not masked:** access control, deletion with the document ([0036](0036-data-lifecycle.md)), and later encryption and the audit log.
- **Telemetry is masked where it is written** (`observability/redact.py`). The run notes the patient identifiers it sees (the expected patient, and the name, ID and date of birth the parse reads). Those values, and each part of a name, are replaced first. Then come patterns for identifiers with a fixed shape: e-mail, Indian mobile numbers, ABHA, Aadhaar, PAN, and hospital and laboratory IDs. This applies to step findings and errors, run errors, job and summary errors, and, by pattern, to every log message.
- **Steps don't copy other stores' content.** The validate and render steps store a SHA-256 and a size (`content_ref`) where they stored the parse, the record and the page. Transcribe and parse steps keep their lines and replies: they are the only copy of what the model produced in that run, and replay ([0035](0035-run-provenance-and-replay.md)) and page reuse read them.
- **`llm.chat` refuses cloud models** unless the documents are marked synthetic (`context.synthetic_documents()`). It never sends one a page image, whatever the marking, because a page shows the patient's name and an image can't be masked. A cloud model is one named like a cloud provider's, an Ollama cloud tag, or one listed in `MEDSUM_CLOUD_MODELS`. A refused model counts as unavailable, so the call moves on to the next one.
- **A leak test** runs every dataset document, and a wrong-patient upload, through the graph with the fake model. It then asserts that no dataset name, ID or date of birth is in a log line or any telemetry column. `python -m evals.redaction` reports recall per identifier type, with intervals.

## Alternatives rejected

- **Redact before the database.** Covered above: the app's purpose is to show this data to the clinician.
- **A named-entity model for names in free text.** It is weak on Indian names, and the run already knows the names that matter. Recall without the known values is 0% for names, and the report shows it.
- **Store every step by reference only.** Replay and page reuse need the transcription and the replies, and no other table keeps them per run.

## Consequences

- Telemetry rows, logs and errors can be shown in a trace screen or shared for debugging without exposing a patient. A name the run never saw, written in free text, is not caught.
- Old steps keep the content they stored before this change, until their documents are deleted.
- Adding a column means choosing its class.
- The note on India's DPDP Act is on the [data-flow page](../data-and-threats.md).

## What would change it

Real patient data, a cloud provider, or names arriving from somewhere the run doesn't see (free-text notes). Each of these would mean pseudonymising identifiers before they leave, with the mapping kept apart.
