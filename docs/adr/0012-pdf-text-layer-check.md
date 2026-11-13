# 0012. The PDF text layer is an independent check, compared per row

- **Status:** Accepted, 2026-11-08; per-row comparison 2026-11-13. Built.

## Context

Validator 1 checks that a value is on the line of the transcription it cites. That catches the parse changing or inventing a value, but not the vision model misreading the page: a misread value is faithfully on its transcribed line. For an image there is no deterministic text to compare with, and a second model reading every page is too slow on a CPU. A digital PDF, though, often has its own text layer.

## Decision

- Where a PDF has a text layer, PyMuPDF's text of each page is stored with its transcription and used **only as a check**, never as the input.
- Every extracted value must appear in that text **in its own row**: near the item's test or medicine name, in a window as wide as its cited row, after removing whitespace and normalising Unicode (text layers keep ligatures and split cells over lines). A value is looked for with the item's own reference ranges taken out of the window, so a misread that lands on a bound of its own range is caught too; a range printed exactly as the value ("Nil") stays in. When the anchor name isn't in the layer, the check falls back to the whole page and flags the name.
- A disagreement is a **flag** (LC-03): both readings are shown to a clinician. Re-parsing can't fix a misread, so it doesn't go to repair.
- A document without a text layer carries an info signal saying its vision reading has no independent check (LC-I1).
- **Misreads on images are an accepted risk.** A cross-check with the second model was weighed and judged too costly for now.

## Alternatives rejected

- **Compare per page** (the first version). A misread that happened to match a number elsewhere on the page passed; the fault check showed it.
- **Use the text layer instead of the vision transcription.** Breaks on scans, and table layout in text layers is unreliable (see [0004](0004-transcribe-then-parse.md)).
- **A second model transcribes every page.** Doubles the slowest step.

## Consequences

- Fault injection catches 110 of 130 injected misreads; every miss is on a page without a text layer.
- The row window may flag a live transcription that spells a row differently from the PDF; live evals measure how often.

## What would change it

Faster inference that makes a second reading affordable, or OCR coordinates that would allow a targeted re-read of one region.
