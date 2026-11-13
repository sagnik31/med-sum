# 0003. Printed values from the model, derived values from code, in one versioned record

- **Status:** Accepted, 2026-11-07. Built.

## Context

Lab values come as "1.5 lakh/cumm", "1,50,000", "<0.5", "Positive". Ranges are labelled, sex-specific, open-ended or several to a test. Units differ between labs. A document can print a report date, a collection date and a print date. Asking a small model for a number, a flag, a conversion or a date invites quiet mistakes that look exactly like correct output.

## Decision

**The record.** One JSONB value per document in `document_records`, with a `schema_version` that must equal the JSON's own; reprocessing replaces it. It is defined once as Pydantic models (`scripts/src/record/`), and the parse and record JSON schemas are exported from them into `scripts/schemas/` (a test fails when they drift). Every item carries `provenance`: run, step, model, prompt, review status and the reasons it was flagged.

**Printed and derived.** Each item has `printed`, exactly what the parse returned (verbatim text and its citation), and `derived`, what code computes. **The model is never asked for a derived value**: the parse schemas have no derived field, and a test enforces it. Derived values are marked as such wherever they are shown.

- **Numbers:** `value_num` only when the whole text is one number, in Indian or international grouping ("2,45,000" is 245000). "<0.5", "2-4", "Nil" keep their text and get no number.
- **Flags:** the printed flag and the flag computed from the range are both kept. The document is never "corrected"; a disagreement goes to a clinician. A flag is computed only when exactly one range is printed, with bounds, in the value's unit; sex- or age-specific ranges are never chosen between.
- **Units:** converted only by code, from a fixed table (`scripts/src/derive/units.py`: units and their spellings by dimension, analytes with the names labs print, a canonical unit, and a molar mass or valence where needed). The printed value stays and is shown by default. An analyte or unit not in the table is never converted, so values in different units never share a trend. Non-factor conversions (HbA1c % and mmol/mol) aren't done.
- **The document date:** a lab report's printed report date, else its collection date; a prescription's printed date. A document with none is **undated**: listed last, never used for a trend or as "the latest", and sent to review so a clinician can add the date. The record stores which rule chose the date. Dates are read day first; two-digit years and impossible dates aren't read.
- **Remarks belong to a test only by position:** on its row or directly under it; otherwise to the panel or the document. Never by meaning ("iron studies" doesn't go with haemoglobin because it sounds related).

## Alternatives rejected

- **Ask the model for numbers, flags, conversions and dates.** Faster to build, and wrong in ways no check can see.
- **Normalise everything to numbers.** Loses what the document says; "<0.5" or "Positive" become unrepresentable.
- **Pick a range from the printed sex or age.** A guess about which range the lab meant.
- **Upload date as the document date.** Says when the paper was scanned, not when the test was done.
- **Normalised tables per item type, or stored FHIR resources.** Many schema changes while the record is still moving, for queries nobody runs yet; FHIR also implies codes the project doesn't assign ([0001](0001-extract-and-display.md)).

## Consequences

- A wrong derived value is a code bug, found by unit tests against hand-written answer keys.
- Some values carry no number, and so no flag or trend. That is the honest result.
- The Go API decodes only citations and provenance, so record fields can change without a Go change. Queries across documents run in code over the records.
- A remark directly under a panel's last row can't be told from one on that row's test; both placements pass.

## What would change it

Values a rule leaves out that it could read without guessing; new analytes or document types with other date meanings; cross-patient query needs that make tables pay off.
