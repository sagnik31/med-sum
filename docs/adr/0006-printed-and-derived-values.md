# 0006. Printed values from the model, derived values from code

- **Status:** Accepted, 2026-11-07. Built.

## Context

Lab values come as "1.5 lakh/cumm", "1,50,000", "<0.5", "Positive" or "Non-reactive". Ranges come labelled, sex-specific, open-ended or several to a test. Asking a small model for a number, a high/low flag or a date invites quiet mistakes that look exactly like correct output.

## Decision

- Every record item has two parts. `printed` is exactly what the parse step returns: verbatim text and its citation. `derived` is what code computes from it: the number, parsed ranges, the flag computed from the range, the analyte, a unit conversion, dose schedules and dates.
- **The model is never asked for a derived value.** The parse schemas contain no derived field at all, and a test enforces it.
- **Numbers:** `value_num` exists only when the whole text is one number in Indian or international grouping ("2,45,000" is 245000). "<0.5", "2-4", "Nil" keep their text and get no number.
- **Flags:** the printed flag and the flag computed from the range are both kept. The document is never "corrected": when they disagree, the value goes to a clinician (LC-10).
- **Multiple ranges:** every printed range is kept. A flag is computed only when exactly one range is printed, with bounds, in the value's unit. Sex- or age-specific ranges are never chosen between.
- Derived values are marked as derived wherever they are shown.

## Alternatives rejected

- **Ask the model for numbers and flags too.** Faster to build, and wrong in ways no check can see, since the model's number has no printed source of its own.
- **Normalise everything into numbers and drop the text.** Loses what the document says, and makes "<0.5" or "Positive" unrepresentable.
- **Pick the applicable range from the printed sex or age.** Plausible, but it is a guess about which range the lab meant; left out until there is a reason to trust it.

## Consequences

- A wrong derived value is a code bug, found by unit tests against hand-written answer keys, not a model error.
- Some values carry no number and so no flag or trend. That is the honest result.

## What would change it

Evidence that a rule leaves out values it could read without guessing; each new rule then gets its own tests.
