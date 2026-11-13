# 0019. Observability in Postgres; logs carry no patient content

- **Status:** Accepted, 2026-11-07; built 2026-11-08 and 2026-11-09.

## Context

A clinician who sees a wrong value, or a developer chasing a regression, needs to know what produced it: which step, model, prompt and retry, and what the validators said. Cloud tracing services would see patient content, and a self-hosted tracing stack needs containers.

## Decision

- **Runs and steps** are tables in the application's database: `pipeline_runs` (one per run: graph version, models used, end state) and `pipeline_steps` (one per node attempt: node, attempt, page, status, model and role, prompt name and hash, input, output, token counts, validator findings, error). Field names follow OpenTelemetry (trace id, span id, parent) so an exporter can be added later.
- **One wrapper writes them** (`traced` in `scripts/src/observability/tracing.py`). It refuses to run a node outside a run, and a test checks every graph node is wrapped, so no node can skip it.
- **Model calls** are rows of their own (`pipeline_model_calls`), one per round trip, failed ones included: provider, model and digest, role, request options, status, token counts and timings, sizes. Never the prompt, the reply or image bytes.
- **Totals are views** over those rows (per run, per document, per node and model, per model), not stored columns.
- **Provenance** on every record item points at the step that produced it, so any value on screen traces back to its model call, prompt and findings.
- **Console and file logs** are JSON lines with run, step and document ids, status and timings. Only allowlisted fields are written; exceptions are logged as type and stack, without their message. Patient content lives only in the database. A test runs a pipeline whose content holds a sentinel and checks it is in the database and nowhere in the log.

## Alternatives rejected

- **LangSmith or another hosted tracer.** Patient content would leave the machine.
- **Self-hosted tracing (Jaeger, Langfuse).** Containers and another store, for one local process.
- **Stored run totals.** Views can't drift from their rows.

## Consequences

- Step rows hold content (inputs and outputs), so the database is a clinical store throughout and protected as one.
- Everything is queryable with SQL; there is no trace UI yet.
- Each run also records its code version and database structure, prompt texts are kept by hash, and a step can be replayed: see [0035](0035-run-provenance-and-replay.md).

## What would change it

A deployment that needs central monitoring, with an OpenTelemetry exporter that sends only the telemetry fields, never content.
