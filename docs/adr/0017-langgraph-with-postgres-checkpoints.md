# 0017. LangGraph, checkpointed in the application's Postgres

- **Status:** Accepted, 2026-11-07; built 2026-11-09 to 2026-11-12.

## Context

A document run takes minutes per page on a CPU, has loops (repair, fallback) and branches (type `other`, errors), and can crash midway. The first version was a linear FastAPI background task that started again from scratch after any failure.

## Decision

- The pipeline is a LangGraph graph (`scripts/src/pipeline/graph.py`): open, transcribe (one page per visit), classify, parse, validate, repair, fallback, render, finish. Nodes are in one module, routing functions in another, small and pure so each branch is unit-tested on its own.
- **Checkpoints** use `langgraph-checkpoint-postgres` in a `langgraph` schema of the application's own database, with the run id as the thread id. A crashed run resumes from its last node, so a page already transcribed isn't transcribed again.
- The checkpoint serializer allows only the types reachable from the graph state; anything else is refused.
- **No hosted tracing.** LangSmith comes in as a dependency and is forced off on import; runs and steps are recorded in Postgres instead (see [0019](0019-observability-in-postgres.md)).
- One Ollama job at a time (see [0018](0018-postgres-job-queue-with-leases.md)).

## Alternatives rejected

- **The linear background task.** No resume, no loops worth reading.
- **A hand-written state machine.** Possible, but checkpointing and resume would have to be built and tested from scratch.
- **A separate checkpoint store (Redis, SQLite).** Another service to run and back up, and the checkpoints would sit outside the database the rest of the data lives in.
- **LangSmith or a self-hosted tracing stack.** A cloud service would see patient content; a self-hosted one needs containers.

## Consequences

- The graph is the project's readable core: each node, each route and each end state is visible and tested.
- Checkpoints hold full state (transcriptions, records) and are not tied to `documents` by a foreign key, so deleting a document doesn't delete its checkpoints.

## What would change it

Several workers on several machines (a job system with its own state might fit better), or a need for parallel page transcription that the CPU can't serve anyway today.
