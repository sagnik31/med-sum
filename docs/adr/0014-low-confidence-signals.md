# 0014. Low confidence is a fixed list of signals; only deterministic ones route

- **Status:** Accepted, 2026-11-09; signals added 2026-11-13. Built.

## Context

"Low confidence" has to mean the same thing to the router, the reviewer and the evals. A model's own confidence score at 4B parameters is not calibrated, and a threshold on it can't be chosen without measured data.

## Decision

- An item or document is low confidence **only** because of a signal in one documented list, `SIGNALS` in `scripts/src/pipeline/signals.py`. Each signal has an id (`LC-nn`), a level (field or document), a routing effect and the reason shown to the reviewer. Reasons are stored as `"LC-nn: <reason>"` or `"LC-nn [field]: <reason>"`, so they are machine-readable and the reviewer text comes from one table.
- **Routing signals are deterministic** (LC-01 to LC-18): an unreadable line, a page not fully transcribed, a value differing from the text layer, a value or medicine not in the source after repair and the fallback, a dropped medicine or results row, a misplaced remark, a parse that never fit its schema, inconsistent units, a flag mismatch, an impossible date, an undated document, a wrong patient, type `other`, a classifier disagreement, a value taken from its own range bound, a model input that may have been cut short to fit the context window. Any one sends the document to `needs_review`.
- A validator **error** becomes a signal only if it survives repair and the fallback model; a **flag** is a signal at once; an **info** finding is shown and never routes (LC-I1 to LC-I3).
- **Operational signals** (OP-1 fallback used, OP-2 repairs needed, OP-3 a model switched off or on by the circuit breaker) describe how the run went, not whether a value is right. They are stored and shown, and never route.
- **Model-judged signals** (MJ-1 an LLM-as-a-judge score, MJ-2 the two models disagreeing) have a slot in the list but are recorded and shown only. Promoting one to routing is a new decision citing eval results that calibrate it.
- Adding a signal means a row in the table, its reviewer text and its test.

## Alternatives rejected

- **A confidence score from the model.** Uncalibrated, and a threshold would be arbitrary.
- **Routing on fallback use.** The fallback model's output is judged by the same validators on its merits (see [0016](0016-fallback-model-and-circuit-breaker.md)).
- **Signals scattered through the code.** The reviewer text and the evals need one place to read them from.

## Consequences

- Every flagged value has a named reason a clinician can read and an eval can count.
- Per-signal precision can be measured once clinicians review documents.

## What would change it

Review outcomes showing a signal is mostly false alarms, or eval data calibrating a model-judged signal well enough to route on.
