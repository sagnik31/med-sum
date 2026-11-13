# 0013. A remark belongs to a test only by its position

- **Status:** Accepted, 2026-11-07. Built.

## Context

One of the first model-written summaries put "Microcytic anaemia" on the WBC count and "Suggest iron studies" on HbA1c. Remarks printed near a table are easy to attach to the wrong row.

## Decision

- A remark belongs to a test only when it is printed on that test's row or directly under it. Otherwise it belongs to the panel or to the whole document. It is never guessed from its content.
- The parse schema has a `scope` for each remark (`test`, `panel`, `document`); code links a test remark to the one observation it names.
- Validator 5 checks the placement against the transcription. A remark attached across rows, panels or headings is an error, sent to repair, then to a clinician (LC-07).

## Alternatives rejected

- **Attach by meaning** ("iron studies" goes with haemoglobin). Plausible, and exactly the kind of inference the project avoids (see [0003](0003-extract-and-display.md)).

## Consequences

- A remark directly under a panel's last row can't be told from a remark on that row's test; both placements pass. Panel and document remarks are shown as such, not pinned on a test.

## What would change it

Layouts where remarks are printed in a column of their own, which would need a rule for that column.
