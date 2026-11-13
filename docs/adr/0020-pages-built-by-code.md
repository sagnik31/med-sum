# 0020. Document pages and the patient summary are built by code

- **Status:** Accepted, 2026-11-12. Built.

## Context

The first versions had the model write each document's page and the patient summary as HTML. The pages invented judgements ("Abnormal" per body system, a hypertension called a presenting complaint), and model-written HTML had to be sanitised, since a document can carry text aimed at the model.

## Decision

- Each document's page (`scripts/src/views/document.py`) and the patient summary (`scripts/src/views/summary.py`) are built by code from the stored records, with no model call.
- Every value is shown exactly as printed, each in an element of its own and escaped. Values computed by code (a flag from the range, a conversion, doses a day, the document date and its rule) are marked derived. All fixed wording comes from one table in code.
- Nothing the record doesn't hold is shown: no "abnormal", no severity, no grouping by organ system.
- A value awaiting review shows its reasons in plain words, from the signal table (see [0014](0014-low-confidence-signals.md)).
- The render node uses the same marking as the finish node, so the page shows exactly the flags that are saved.
- The UI shows each page in a sandboxed iframe with scripts disabled and a content security policy that blocks every remote load.

## Alternatives rejected

- **Model-written HTML with a sanitiser.** Contains the HTML, not the invented content. The sanitiser and the HTML prompt were removed.
- **A frontend that renders the record itself.** Planned for the clinician screens; until then the server-built page keeps one renderer, tested against every answer key.

## Consequences

- Rendering takes no model time (it was about 90 seconds a document).
- Tests check that every text node on a page is a record string, a record number or the view's own words, and that no judgement word the record lacks appears.

## What would change it

The clinician UI rendering records directly; the same rules then apply there.
