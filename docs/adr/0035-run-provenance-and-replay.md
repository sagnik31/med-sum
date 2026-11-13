# 0035. Each run records its code and schemas, prompts are stored by hash, and a step can be replayed

- **Status:** Accepted, 2026-11-13. Built.

## Context

A step already records its model and the hash of its prompt ([0019](0019-observability-in-postgres.md)). That says *whether* two steps used the same prompt, but not what the prompt said, so an old step can't be run again once the prompt file has changed. A run also didn't say which code, output schema or database structure produced it: the model calls record only that structured output was on. So a change in results couldn't be tied to a commit, a schema change or a migration.

## Decision

- **Every run row records what it was made with.** `code_version` is the git commit the AI service's code is at, and `code_dirty` says whether the working tree had changes. Both are read once per process. `MEDSUM_CODE_VERSION` gives the version where there is no git checkout; `code_dirty` is then null. A version that can't be found is null, not a guess. `schema_hash` is a SHA-256 of the JSON schemas the models are sent as structured output (the classifier's and each parse's), so a change to the record's models shows even in a dirty tree. `db_schema_hash` is a SHA-256 of the database's public tables and views, with each column's name, type and nullability, read from `pg_catalog`. It describes the database the run used, not the schema file, and it reads the same for every role.
- **Prompt texts are stored by hash.** A `prompts` table (hash, name, text, first use) gets a row the first time any step uses a prompt, in the same transaction that closes the step. Prompts are the pipeline's own instructions, never document content, so their rows don't go away when documents are deleted. The AI service's role may read and add rows, not change them.
- **`python -m pipeline.replay <step_id>`** rebuilds one step's model call from the database and runs it again on the models configured now. It uses the stored prompt text, the step's input, and the run's stored transcriptions; for a transcribe step it renders the page from the document's file and checks it against the image hash the step stored. It prints the stored and new replies as a diff, and which model answered each. It writes nothing. Transcribe, classify and parse steps can be replayed.

## Alternatives rejected

- **Hash `db/schema.sql`.** That hash describes the file, not the database. A database loaded from an older file, or changed by hand, would carry the new hash.
- **Store the full prompt on every step.** The same text would be stored thousands of times; one row per hash is enough.
- **Store each step's full model input (the messages).** Page images and transcriptions are already stored once. Storing them again per call would double what has to be deleted with a document, and replay can rebuild the input from them.
- **Replay repair steps.** A repair's conversation includes the validators' problems as text, which no step stores. Rebuilding them would mean running the validators again on an old record. Repair is left out until there is a reason to replay it.

## Consequences

- A result can be traced to a commit, an output schema, a database structure, the exact prompt texts and the model digests (the model calls' rows).
- Replay shows how a prompt or model change moves one step's reply, without running the whole document. It prints document content to the terminal, so it is a developer's tool, not a service endpoint.
- Runs made before this change have null versions, and their prompts weren't stored, so they can't be replayed.
- A transcribe step that read its page again in halves ([0034](0034-capped-page-read-again-in-halves.md)) is replayed as the single whole-page call.

## What would change it

A need to replay repairs, which would mean storing the problems a repair was sent. Or a deployment without git that doesn't set `MEDSUM_CODE_VERSION`, which would mean stamping the version into the build.
