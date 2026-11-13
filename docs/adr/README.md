# Architecture decision records

Why Med-Sum is built the way it is: the ten decisions the rest of the design follows from. Each record gives its context, what was decided, the alternatives rejected, the consequences, and the evidence that would change it. Its status says when it was decided and whether all of it is built yet.

| # | Decision | Status |
|---|---|---|
| [0001](0001-extract-and-display.md) | Extract and display for hospital staff; never infer | Built; patient records not yet |
| [0002](0002-two-model-steps.md) | Two model steps: transcribe each page, then parse it in parts | Built |
| [0003](0003-printed-and-derived-values.md) | Printed values from the model, derived values from code, in one versioned record | Built |
| [0004](0004-deterministic-checks-decide-routing.md) | Deterministic checks decide what a clinician must review | Built |
| [0005](0005-local-models-with-fallback.md) | Local models with a fallback and a circuit breaker; cloud only for synthetic text | Built |
| [0006](0006-durable-traceable-runs.md) | Durable, traceable runs: LangGraph and a job queue in the application's Postgres | Built |
| [0007](0007-pages-built-by-code.md) | Document pages and the patient summary are built by code, with one checked model overview | Built |
| [0008](0008-patient-data-protection.md) | Patient data: protected where it must live, masked where it leaves, deleted with its document | Built, except the Go backend's role |
| [0009](0009-measured-evals.md) | Evals measure the safety net, on a committed synthetic dataset, with honest uncertainty | Built; live runs not made yet |
| [0010](0010-staff-access-and-security.md) | Staff access: admin-issued accounts, server-side sessions, permissions in code, scope in SQL | Partly built (see its status) |

Where patient data lives and the threats considered are on the [data-flow page](../data-and-threats.md).

## How these records are kept

- One file per decision, `00NN-<kebab-name>.md`, with Status, Context, Decision, Alternatives rejected, Consequences, and What would change it.
- Records hold decisions only, never plans or open work. A decision that changes is edited in place with its status saying what changed and when, or replaced by a new record that the old one names.
- Code that follows a decision says `See docs/adr/00NN-<name>.md`, and its comment also states the rule in a sentence or two, so the code reads on its own.
