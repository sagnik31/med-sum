# 0036. Checkpoints go with their run, and a file uploaded twice is flagged

- **Status:** Accepted, 2026-11-13. Built.

## Context

Deleting a document cascades through every table in `public`. LangGraph's checkpoints live in their own schema, keyed by thread (the run's id), with no foreign key to `documents`, and they hold the graph's whole state: page transcriptions, the parse and the record. A deleted document's content stayed in them for good. They were also kept after a run ended, although an ended run never resumes.

A file uploaded twice for the same patient became two documents, and the patient summary counted its values twice: a trend could rest on one report read two times.

## Decision

- **A run's checkpoints are deleted when the run ends and when the run is deleted.** Two triggers on `pipeline_runs` (the run gets an `ended_at`; the row is deleted, which happens when its document is deleted) call one `SECURITY DEFINER` function. The function deletes the thread's rows from the checkpoint tables, so the role that deletes a document needs no rights on the checkpoint schema. The AI service creates the checkpoint tables after `db/schema.sql` is loaded, so the function checks that each table exists when it runs.
- **The document worker deletes the thread too, once the graph has stopped.** This catches a checkpoint written after the document was deleted mid-run. A crashed worker deletes nothing, so its run can still resume from the last checkpoint.
- **Every upload's SHA-256 is stored** (`documents.sha256`). It is computed while the file is written and never sent to clients. An upload with the same hash as an earlier document of the same patient is accepted, and the API names the earlier one as `duplicate_of`. Until patients are records of their own, the patient is the account that uploaded the file.
- **The patient summary leaves out a duplicate**, so the earliest upload stands for the file and its values count once.

## Alternatives rejected

- **Refuse a duplicate upload.** A second copy can be deliberate, for example a re-upload after the first was deleted. A flag keeps the choice with the user.
- **Keep checkpoints until a periodic clean-up.** Content would outlive its document for however long the clean-up interval is. A trigger deletes it in the same transaction.
- **A foreign key from the checkpoint tables to `pipeline_runs`.** Those tables belong to the LangGraph package, which creates and migrates them itself.

## Consequences

- A deleted document leaves no row that names it in any schema. A test runs a job, deletes the document, and searches every table, including the checkpoint blobs.
- A job whose run has ended can't be resumed from a checkpoint. A retry is a new run, as it already was.
- Two different files with the same content (a re-scan saved byte for byte) are treated as one. A re-scan that differs by one byte is not flagged.

## What would change it

Patients as records of their own: duplicates would then be checked per patient record, not per uploading account.
