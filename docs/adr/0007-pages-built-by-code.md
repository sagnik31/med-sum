# 0007. Document pages and the patient summary are built by code, with one checked model overview

- **Status:** Accepted, 2026-11-09 (summary rules) and 2026-11-12 (built); layout kept as built, 2026-11-13.

## Context

The first versions had the model write each document's page and the patient summary as HTML. They invented judgements ("Abnormal" per body system, a hypertension called a presenting complaint), claimed trends from one value, and placed medicines before the labs they followed. Model-written HTML also had to be sanitised, since a document can carry text aimed at the model. A doctor reads the summary quickly before a visit and has to be able to trust it.

## Decision

**Pages are code** (`scripts/src/views/`), built from the stored records with no model call. Every value is shown exactly as printed, escaped, in an element of its own; derived values are marked; all fixed wording comes from one table. Nothing the record doesn't hold is shown: no "abnormal", no severity, no organ-system grouping. A value awaiting review shows its reasons in plain words. The render node marks the record exactly as the finish node saves it. The UI shows pages in a sandboxed iframe with scripts off and a content security policy that blocks remote loads.

**The summary's facts are computed** (`scripts/src/summary/`) from every finished document (`verified` or `needs_review`), ordered by printed date, undated last, numbered D1..Dn; every value cites `Dn pP:L`.

- **Sections:** the patient and warnings (undated documents, values awaiting review, documents not included); values flagged or out of range in the latest report of each test, and allergies; the medicines "on the latest prescription" (never "current"), then earlier ones; diagnoses exactly as printed; results and vitals over time under their printed panels; remarks with what they are printed on; every document with its review state.
- **Trends are only "rising" or "falling"**: two or more dated values of a test in one printed unit, not awaiting review, one per date, each higher (or lower) than the last. Never "improving" or "worsening".
- **No confidence labels:** "based on N reports", naming them.
- **Flagged documents are included**, each flagged value marked "awaiting review" with its reason and left out of trends. Leaving them out would hide what most needs a look.
- A file uploaded twice for a patient counts once.

**The overview** is the only model-written text: a few sentences from the computed facts alone. Code checks it: every number must be a value of the facts and stand near a word of what it belongs to, a name with digits ("T4") must appear whole, and every word must be a word of the facts or of a fixed list of ordinary words. A failed reply is sent back once with what failed; if it fails again it is **dropped, never edited**, and the summary says why.

## Alternatives rejected

- **Model-written HTML with a sanitiser.** Contains the HTML, not the invented content; the sanitiser was removed with it.
- **A model-written summary checked afterwards.** Too much free text to check reliably.
- **Trend words with clinical meaning.** Whether a change is better or worse depends on the test and the patient.
- **Editing a failed overview.** Code would become the author of a sentence nobody checked.

## Consequences

- Rendering takes no model time. Tests check every text node is a record string, a record number or the view's own words, and that no judgement word the record lacks appears.
- The overview check is literal: two tests sharing a unit in one list can still trade values, and an ordinary word missing from the list drops a good overview.

## What would change it

Clinician feedback on the layout once the review screens exist; the clinician UI rendering records itself, under the same rules; a meaning-level check measured as well as this one.
