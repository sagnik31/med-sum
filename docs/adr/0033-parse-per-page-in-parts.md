# 0033. The parse reads a page, or part of a long page, per call

- **Status:** Accepted, 2026-11-13. Built.

## Context

The parse step (see [0004](0004-transcribe-then-parse.md)) first sent the whole numbered transcription in one call, with its reply capped at 8,192 output tokens. A parse reply grows with every item it holds. One live parse of a 26-result, three-page lab report with `qwen3.5:4b` took 5,084 output tokens, about 195 per result, so the cap is reached near 40 results. Lab reports with 40-60 results are common. A reply cut off by the cap doesn't fit the schema, so a long report could never parse. A prompt cut to fit the context window also routed nowhere: the call row recorded it, and nothing sent the document to review.

## Decision

- **One call per part.** Each page with text is a part. A page with more than 25 rows (results-table rows and medicine lines) is cut into parts of even size, between rows, never inside one. A cut at a table's first data row moves up to the table's header, or the heading above it, so a table is not split from its header when it doesn't have to be.
- **A part that starts inside a table gets context.** The nearest heading above (the panel's name) and the table's header row and separator are shown before the part's own lines, with their page line numbers, so citations still point at the page.
- **Code merges the replies.** The patient's and the document's printed details take each field from the first part that has it (page headers repeat them); clinicians are listed once each; every list of items is the parts' lists in order. Item ids and remark links were already code's job.
- **Each part is a step of its own**, with its page and attempt. Each item's provenance names the step of the part that read it.
- **Repair asks again only the parts that were wrong**: a part whose reply didn't fit the schema, or a part holding a line an error finding points at. The other parts keep their replies.
- **A cut prompt routes.** A transcription or parse call whose prompt may have been cut to fit the context window raises LC-18 and sends the document to review (see [0014](0014-low-confidence-signals.md)).

## Alternatives rejected

- **Raise the output cap.** It only moves the ceiling, and a longer reply takes longer on a CPU with no gain.
- **One call per table.** Text outside tables (patient details, remarks, prescriptions' medicine lines) would need parts of their own, and a remark after a table belongs to it by position (see [0013](0013-remarks-attach-by-position.md)), so cutting at every table splits what the model must read together. A page is the natural unit; a long page is cut only where it must be.
- **Let the model see the whole document and reply per part.** The input would still grow with the document, and each call would repeat it.

## Consequences

- No reply grows past about 25 rows, well under the cap.
- A document of n pages makes at least n parse calls. Each call repeats the parse prompt, so short documents cost a little more input than before.
- A remark that refers to a test on another page is still read on its own page; it attaches by position as before.
- The dataset has a 60-result report whose first two pages are parsed in two parts each.

## What would change it

A live eval showing parts losing items at their cuts, or a model whose replies fit a whole long report well under its cap.
