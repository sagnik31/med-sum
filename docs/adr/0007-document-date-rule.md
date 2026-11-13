# 0007. The document date comes from a fixed rule; undated documents go to review

- **Status:** Accepted, 2026-11-07; the review routing confirmed 2026-11-13. Built.

## Context

A lab report can print a report date, a sample collection date, a registration date and a print date. The summary orders documents and computes trends by date, and upload order is not the order things happened in.

## Decision

- **Lab reports:** the printed date of when the report was generated ("Reported on"), otherwise the sample collection date. **Prescriptions:** their printed date.
- A document with none of these is **undated**: listed last, labelled undated, and never used for a trend, as "the latest" value, or in any ordering claim.
- The record stores which rule chose the date (`date_source`).
- Dates are read day first; two-digit years and impossible dates are not read. A printed date code can't read is skipped by the rule, and the date check flags it.
- An undated document ends in review (LC-12), so a clinician can add the date.

## Alternatives rejected

- **Upload date.** Says when someone scanned the paper, not when the test was done.
- **Let the model pick the date.** It would pick inconsistently, and the choice couldn't be explained.
- **An undated document as a warning only, ending verified.** It would then stay out of every trend with nobody asked to fix it.

## Consequences

- Ordering is predictable and explainable: the page says which printed date was used.
- A document with an unreadable date needs a clinician before it counts in trends.

## What would change it

Document types with other date semantics (a discharge summary's admission and discharge dates), or month-first documents in real use.
