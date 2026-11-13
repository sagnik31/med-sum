# 0021. Patient summary: computed facts, honest trends, a checked overview

- **Status:** Accepted, 2026-11-09 (rules) and 2026-11-12 (built); layout kept as built, 2026-11-13.

## Context

A doctor reads the summary before a visit, quickly, and has to be able to trust it. The model-written summaries it replaced claimed "worsening" from one value, "high confidence" from one report, and placed medicines before the labs they followed.

## Decision

- **Facts in code** (`scripts/src/summary/`): from the records of every finished document (`verified` or `needs_review`). Documents are ordered by their printed date (see [0007](0007-document-date-rule.md)), undated last, and numbered D1..Dn; every value cites `Dn pP:L`.
- **Sections:** the patient and warnings (undated documents, values awaiting review, documents not included); values with a printed flag or outside their range in the latest report of each test, and allergies; the medicines on the latest prescription ("on the latest prescription", never "current"), then earlier ones; diagnoses and impressions exactly as printed; results and vitals over time under their printed panels; remarks with what they are printed on; every document in date order with its review state.
- **Trends** are only "rising" or "falling": two or more dated values of a test, in the same printed unit, not awaiting review, one per date, each higher (or lower) than the last. Never "improving" or "worsening", which are clinical judgements.
- **No confidence labels.** A trend says "based on N reports" and names them.
- **Flagged documents are included.** Each flagged value reads "awaiting review" with its reason and a link to its source; it is left out of trends. Leaving them out would hide exactly the values that need a look.
- **The overview** is the only model-written part: a few sentences from the computed facts alone. Code checks it: every number must be a value of the facts and stand near a word of what it belongs to, a name with digits ("T4") must appear whole, and every word must be a word of the facts or of a fixed list of ordinary words. A reply that fails is sent back once with what failed; if it fails again it is **dropped, never edited**, and the summary says the overview was left out and why.

## Alternatives rejected

- **A model-written summary checked afterwards.** Too much free text to check reliably.
- **Trend words with clinical meaning.** "Worsening" depends on the test and the patient; that is the doctor's call.
- **Editing a failed overview.** Would make code the author of a sentence nobody checked.

## Consequences

- The check is literal: two tests that share a unit in one list can still trade values, and an ordinary word missing from the list drops a good overview.
- The summary is rebuilt after each batch of documents finishes, for users who asked for one.

## What would change it

Clinician feedback on the layout once the review screens exist, or a check that compares meanings rather than words and is measured as well as this one.
