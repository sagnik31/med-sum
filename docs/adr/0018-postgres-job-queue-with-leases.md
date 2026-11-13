# 0018. A Postgres job queue with leases and claim-conditional writes

- **Status:** Accepted, 2026-11-12 (documents) and 2026-11-13 (the patient summary). Built.

## Context

Jobs used to live in FastAPI `BackgroundTasks`, so a restart lost them. Job ownership was a heartbeat column matched on the document id alone, so a worker whose job had been taken over could still overwrite the new owner's result or mark it failed.

## Decision

**Documents** (`scripts/src/document_jobs.py`):

- A `jobs` table with status (queued, running, done, failed, parked), attempts, `claimed_by`, `lease_until` and the run id. At most one open job per document, by a partial unique index.
- One worker thread per service process takes the oldest queued job, or a running one whose lease ran out, with `FOR UPDATE SKIP LOCKED`. One worker is also what keeps Ollama to one job at a time.
- A heartbeat renews the 5-minute lease every minute. **Every write** (lease renewal, run id, result, failure) is conditional on the claim and raises `LostClaim` when the claim is gone.
- A job claimed three times without finishing is **parked**, so a document that crashes the worker can't loop.
- A taken-over job resumes from the graph's checkpoint (see [0017](0017-langgraph-with-postgres-checkpoints.md)).

**The patient summary** keeps a simpler lock on its user row: a summary with no progress for 30 minutes can be taken over by a new request. The claim's own timestamp (`patient_insights_updated_at`) is its token: the result and failure writes match on status `processing` and that timestamp, so a run that lost its claim writes nothing and logs `summary_claim_lost`.

## Alternatives rejected

- **A message broker (Redis, RabbitMQ).** Another service to run; Postgres already gives row locks and transactions, and the scale is one document at a time.
- **Keep `BackgroundTasks`.** Loses jobs on restart, including on every `--reload`.
- **A new claim column for the summary.** Would need a schema reset for a mechanism that moves when patients get records of their own; the existing timestamp does the job.

## Consequences

- A killed worker's job is taken over after its lease and finishes from its checkpoint; a test kills one mid-run and checks the late write is refused.
- The summary still has no queue of its own: it is rebuilt in the worker thread after a batch of documents finishes.

## What would change it

Many workers or machines, where a dedicated queue's features (priorities, delays, visibility) start to matter.
