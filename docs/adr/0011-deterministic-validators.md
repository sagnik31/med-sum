# 0011. Validators are deterministic code, never a model

- **Status:** Accepted, 2026-11-07; checks added 2026-11-13 (range bounds, results-table rows). Built.

## Context

The pipeline's safety rests on catching what the model gets wrong. A model judging another model's output (or its own) is neither repeatable nor calibrated at this size. Plain code can check a record against the transcription it cites, every time, in milliseconds.

## Decision

`validate(record, expected_patient=..., today=...)` (`scripts/src/validators/`) runs these checks in order and returns findings (validator, severity, message, item, field, page, lines):

1. **Source:** every extracted value appears on its cited lines, as whole tokens after Unicode (NFKC) and whitespace normalisation, case-sensitive ("10" is not in "10.4"). A quote may be the whole cited line or an exact whole-token part of it.
2. **Text layer:** where the PDF has one, each value is also in the PDF's own text, in its own row (see [0012](0012-pdf-text-layer-check.md)).
3. **Units and ranges:** same unit family, low below high, the value's unit matching the range's. **3b:** a value equal to one of its own range bounds, printed nowhere else on its row, was probably read from the range (LC-16).
4. **Flags:** the printed flag against the flag computed from the range.
5. **Remark placement:** a remark sits on its test's row or directly under it (see [0013](0013-remarks-attach-by-position.md)).
6. **Medicines:** every medicine line is in the record and every record medicine is on a line. **6b:** every data row of a lab results table is cited by a result (LC-17).
7. **Dates:** real dates, collection not after the report.
8. **Coverage:** transcription lines no item uses, apart from known boilerplate. Shown in review; never a failure on its own.
9. **Patient match:** the printed patient details against the patient the document was uploaded to.

Each finding has a severity that decides routing:

- **error:** the parse may be wrong, so a re-parse can fix it (1, 5, 6, 6b). Goes to repair first.
- **flag:** the document itself is in doubt, which re-parsing can't change (2, 3, 3b, 4, 7, 9). Goes to a clinician.
- **info:** shown in review only (8, ranges or flags code can't read, units not in the table).

Findings hold patient content, so they are stored in the database only, never logged.

## Alternatives rejected

- **An LLM-as-a-judge** as the check. Not repeatable, not calibrated; kept as a possible recorded-only signal (see [0014](0014-low-confidence-signals.md)).
- **Fuzzy matching of values.** Would let "10.4" pass for "10". Exact tokens are strict on purpose; spacing and Unicode forms are the only things ignored.

## Consequences

- The checks are measured as detectors by fault injection (see [0026](0026-eval-method.md)), so their blind spots are known and named, not guessed.
- Known weaknesses: a misread on a page with no text layer passes if the parse copies it faithfully; validator 2 compares squashed text, so a one-letter value can match inside another cell; results printed as lines of text, not a table, have no row check.

## What would change it

A fault class the checks keep missing, or a clean document they keep flagging, measured by the fault check or the live evals.
