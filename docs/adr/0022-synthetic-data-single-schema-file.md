# 0022. Synthetic data only, one schema file, no migrations

- **Status:** Accepted, 2026-11-07. Built.

## Context

The schema changes often while the pipeline takes shape. Migrations earn their keep when a database holds data that must survive a change. This project's databases hold synthetic documents only, which can be loaded again at any time.

## Decision

- The databases hold **synthetic data only**. The repository's dataset is fictional; real patient data must not be loaded.
- The whole schema is one hand-written file, `db/schema.sql`. There are no migrations: a change edits the file, then the development database is reset (`python -m reset_db --yes` from `scripts/src`).
- `reset_db` lists what it would delete without `--yes`, works only on a database on the local machine, drops the `public` and `langgraph` schemas, loads the schema file, sets up the checkpoint tables, re-grants the AI service's role when it exists, and deletes uploaded files. It never touches anything else on disk.
- Both test suites build throwaway databases from the same file and drop them afterwards.

## Alternatives rejected

- **A migration tool** (golang-migrate, Alembic). A migration per change for data nobody needs to keep.

## Consequences

- Every schema change is a reset of the development database, which is irreversible and so is done deliberately.
- Before any real deployment, migrations must be introduced and the schema file becomes their baseline.

## What would change it

Any database whose data must outlive a schema change.
