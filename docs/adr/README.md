# Architecture decision records

Why Med-Sum is built the way it is. Each record is one decision: its context, what was decided, the alternatives rejected, the consequences, and the evidence that would change it ([0001](0001-record-architecture-decisions.md) explains the format). Code that follows a decision cites its record as `See docs/adr/00NN-<name>.md`.

A decision that changes gets a new record, or its status says what superseded it. Records say what is decided, and their status says whether it is built yet.

## Foundations

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted |
| [0002](0002-hospital-staff-are-the-users.md) | Hospital staff are the users; patients are records | Accepted; patient records not built yet |
| [0003](0003-extract-and-display.md) | Extract and display; never infer | Accepted |

## Document pipeline

| # | Decision | Status |
|---|---|---|
| [0004](0004-transcribe-then-parse.md) | Two model steps: transcribe, then parse | Built |
| [0005](0005-document-types-and-classification.md) | Two document types, classified by the model with a keyword check | Built |
| [0006](0006-printed-and-derived-values.md) | Printed values from the model, derived values from code | Built |
| [0007](0007-document-date-rule.md) | The document date comes from a fixed rule; undated documents go to review | Built |
| [0008](0008-units-converted-by-code.md) | Units converted only by code, from a fixed table | Built |
| [0009](0009-no-medical-codes-or-brand-mapping.md) | No medical codes and no brand-to-generic mapping | Accepted |
| [0010](0010-record-as-versioned-jsonb.md) | One JSONB record per document, with a schema version | Built |
| [0011](0011-deterministic-validators.md) | Validators are deterministic code, never a model | Built |
| [0012](0012-pdf-text-layer-check.md) | The PDF text layer is an independent check, compared per row | Built |
| [0013](0013-remarks-attach-by-position.md) | A remark belongs to a test only by its position | Built |
| [0014](0014-low-confidence-signals.md) | Low confidence is a fixed list of signals; only deterministic ones route | Built |
| [0015](0015-repair-re-parses-only.md) | Repair re-parses; it never re-transcribes | Built |
| [0016](0016-fallback-model-and-circuit-breaker.md) | A fallback model, and a circuit breaker that counts only infrastructure failures | Built |
| [0017](0017-langgraph-with-postgres-checkpoints.md) | LangGraph, checkpointed in the application's Postgres | Built |
| [0018](0018-postgres-job-queue-with-leases.md) | A Postgres job queue with leases and claim-conditional writes | Built |
| [0019](0019-observability-in-postgres.md) | Observability in Postgres; logs carry no patient content | Built |
| [0020](0020-pages-built-by-code.md) | Document pages and the patient summary are built by code | Built |
| [0021](0021-patient-summary-rules.md) | Patient summary: computed facts, honest trends, a checked overview | Built |
| [0033](0033-parse-per-page-in-parts.md) | The parse reads a page, or part of a long page, per call | Built |

## Data, operations and evals

| # | Decision | Status |
|---|---|---|
| [0022](0022-synthetic-data-single-schema-file.md) | Synthetic data only, one schema file, no migrations | Built |
| [0023](0023-fail-closed-settings-and-database-roles.md) | Database settings fail closed; each service gets a least-privilege role | AI service built; Go backend role not yet |
| [0024](0024-local-inference-by-default.md) | Local inference by default; a cloud model only for synthetic data | Local built; cloud option deferred |
| [0025](0025-synthetic-eval-dataset.md) | A synthetic dataset with answer keys, generated and committed | Built |
| [0026](0026-eval-method.md) | Eval method: measure the safety net, keep the numbers honest | Built; live runs not made yet |

## Security

| # | Decision | Status |
|---|---|---|
| [0027](0027-staff-accounts-and-passwords.md) | Staff accounts: email and password, created by an admin | Built, except the per-IP rate limit |
| [0028](0028-server-side-sessions.md) | Server-side sessions in an HttpOnly cookie, not a JWT | Built |
| [0029](0029-csrf-protection.md) | CSRF: SameSite cookies, a custom header and an origin check | Built |
| [0030](0030-authorization-in-two-layers.md) | Authorization in two layers: permissions in code, organisation scope in SQL | Permissions built; organisation scope on patient data not yet |
| [0031](0031-append-only-audit-log.md) | An append-only audit log of every patient-data read and write | Not built yet |
| [0032](0032-ai-service-behind-the-api.md) | The browser never calls the AI service | Built, apart from the localhost bind and moving dev secrets out |
