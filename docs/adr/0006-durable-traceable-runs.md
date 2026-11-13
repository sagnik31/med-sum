# 0006. Durable, traceable runs: LangGraph and a job queue in the application's Postgres

- **Status:** Accepted, 2026-11-07 to 2026-11-13. Built; there is no trace screen yet.

## Context

A document run takes minutes per page, has loops (repair, fallback) and branches, and can crash midway. The first version was a linear FastAPI background task: a restart lost the job, a failure started again from scratch, and a worker whose job had been taken over could still overwrite the new owner's result. A clinician who sees a wrong value, or a developer chasing a regression, needs to know what produced it, and cloud tracing services would see patient content.

## Decision

**The graph.** The pipeline is a LangGraph graph (`scripts/src/pipeline/graph.py`): open, transcribe (one page per visit), classify, parse (one part per visit), validate, repair, fallback, render, finish. Nodes live in one module and routing functions in another, small and pure, so each branch is unit-tested. Checkpoints use `langgraph-checkpoint-postgres` in a `langgraph` schema of the application's database, with the run id as the thread id, so a crashed run resumes from its last node. The checkpoint serializer allows only types reachable from the graph state.

**The job queue** (`scripts/src/document_jobs.py`): a `jobs` table (queued, running, done, failed, parked) with at most one open job per document. One worker thread per service process takes the oldest queued job, or one whose lease ran out, with `FOR UPDATE SKIP LOCKED`; one worker also keeps Ollama to one job at a time. A heartbeat renews a 5-minute lease every minute, and **every write is conditional on the claim** (`LostClaim` otherwise). A job claimed three times without finishing is parked. The patient summary keeps a simpler lock on its account row, with the claim's own timestamp as its token: its result and failure writes match on that timestamp, so a run that lost the claim writes nothing.

**Tracing in Postgres.** One wrapper (`traced`) writes every node attempt to `pipeline_steps` (node, attempt, page, status, model and role, prompt hash, input, output, tokens, findings, error) under a `pipeline_runs` row; it refuses to run outside a run, and a test checks every node is wrapped. Field names follow OpenTelemetry. Each model round trip is a `pipeline_model_calls` row (model and digest, options, status, tokens, timings, sizes; never prompt, reply or image). Totals are views. Every record item's provenance points at its step.

**Reproducible.** Each run records its git commit and whether the tree was dirty (`MEDSUM_CODE_VERSION` where there is no checkout), a hash of the JSON schemas sent to the models, and a hash of the database's actual structure read from `pg_catalog`. Prompt texts are stored once per hash in `prompts`. `python -m pipeline.replay <step_id>` rebuilds a transcribe, classify or parse call from the database, runs it on today's models and prints a diff; it writes nothing.

**Logs** are JSON lines with ids, statuses, counts and timings from an allowlist; exceptions are logged as type and stack without their message. LangSmith, a dependency of LangGraph, is forced off.

## Alternatives rejected

- **A hand-written state machine.** Checkpointing and resume would have to be built and tested from scratch.
- **A message broker or a separate checkpoint store.** Another service to run; Postgres already has row locks and transactions, and the scale is one document at a time.
- **Hosted tracing (LangSmith) or a self-hosted stack.** The first sends patient content off the machine; the second needs containers.
- **Hashing the schema file instead of the database.** Describes the file, not the database the run used.
- **The full prompt on every step.** The same text thousands of times; one row per hash is enough.

## Consequences

- A killed worker's job is taken over after its lease and finishes from its checkpoint; a test kills one mid-run and checks its late write is refused.
- Any value on screen traces to its step, model call, prompt text and code version. Repair steps can't be replayed (their problem text isn't stored).
- Step rows hold content, so the database is a clinical store throughout; checkpoints are deleted when their run ends ([0008](0008-patient-data-protection.md)).

## What would change it

Several workers on several machines; central monitoring, through an OpenTelemetry exporter that sends only telemetry fields.
