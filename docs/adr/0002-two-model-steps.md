# 0002. Two model steps: transcribe each page, then parse it in parts

- **Status:** Accepted, 2026-11-08; repair narrowed, parsing in parts and the half-page re-read added 2026-11-13; a tool-using repair added behind config 2026-11-13, not yet measured. Built.

## Context

A vision-language model can turn a page image straight into JSON, but then the validators have nothing to check values against: the image is the only source, and no code can read it. Small models also read images worse when they must produce a strict schema at the same time. And output is capped per call, so a long page or a long report can be cut off.

## Decision

**1. Transcribe.** Each page image goes to the vision model with one generic prompt and comes back as numbered lines (Markdown tables kept). Every document takes this path, even a PDF with a text layer, so a scan and a digital PDF behave the same. The transcription is the anchor for every citation and the text the checks compare with; a PDF's own text layer is read alongside as an independent check only ([0004](0004-deterministic-checks-decide-routing.md)).

- **A page cut off by the output cap is read again once, in two halves.** After a cap on every model, the image is cut into the top and bottom 57.5% (the middle 15% is read twice), each read with the same prompt and cap. The halves are joined in code only on an exact run of at least two lines that the top ends with and the bottom starts with, allowing up to three lines at each cut edge, found in one place only. Anything else (a half capped again, no shared run, a run in two places) keeps the page unread, with LC-02. Every call is on the page's step.

**2. Classify.** The model picks a label from a fixed list (`lab_report`, `prescription`, `other`), and a keyword check backs it up. Keywords have an opinion when they find at least 2 signals for a type and twice as many as for the other. The model's label wins when the keywords agree or have no opinion; the keywords win when the model gave no usable label or said `other`; when the two name different parsed types, the document is `other` and a review signal (LC-15) records the disagreement, since the wrong schema would be used.

**3. Parse.** A text-only call fills in the type's schema from the numbered transcription, with Ollama's JSON-schema `format`; every item cites its page and lines.

- **One call per part.** Each page is a part; a page with more than 25 rows (results-table rows and medicine lines) is cut into even parts between rows. A cut at a table's first data row moves up to its header. A part that starts inside a table is shown the heading, header row and separator above it, with their page line numbers.
- **Code merges the replies:** header details from the first part that has them, clinicians once each, item lists in order. Each part is a step, and each item's provenance names its part's step.

**4. Repair re-parses; it never re-transcribes.** Only validator *errors* send a parse back (flags can't be fixed by re-parsing). The previous reply goes back with each problem listed, and only the parts with problems are asked again. Budget: the first parse plus two repairs per model; then the fallback model gets the same budget once ([0005](0005-local-models-with-fallback.md)); then surviving errors become review signals.

- **A second repair, chosen by config (`MEDSUM_REPAIR=tools`; the fixed re-parse is the default).** The same request, plus read-only tools over the document (`get_lines`, `search_transcription`, `get_text_layer`, `get_item`; `scripts/src/tools/`, kept apart from the graph so other clients can use them). The model may read before it answers, within a budget of model turns per repair that includes the turn giving the fix (default 3, so at most two turns of reading). A malformed call, or a call to a tool that isn't offered, uses its turn, and its error goes back to the model. The last turn has no tools and enforces the schema, with the tool exchange written into its request. Nothing else changes: the fix is checked against the schema, the validators judge the merged parse, and the attempt budget and end states are the same. Each tool call is a step of its own that stores the tool, the outcome and sizes, never what the tool returned. Tools run only inside a repair, so a document that parses cleanly costs no more; re-reading a page image is not offered, since it takes minutes.

## Alternatives rejected

- **One step, image to JSON.** Nothing to check against; misreads happen either way, and one step only hides them.
- **The text layer as the main input.** Scans and photos have none, and table layout in text layers is unreliable.
- **Re-transcribe a page a finding points at.** At temperature 0 the same model reads the same image the same way, after minutes per page, and no finding tells a misread from a parse error. The capped page is the exception because its reading is known to be incomplete and a smaller image needs fewer tokens.
- **Raise the output caps.** They stop a looping model; raising them only moves the ceiling.
- **One parse call per table.** Text outside tables (headers, remarks, medicine lines) would need parts of its own, and a remark belongs to what it is printed under, so a page is the natural unit.
- **Tools offered at every parse, or a model-driven loop over the whole pipeline.** Either would add model turns to every document; the tools are confined to repair, where an error is already known.
- **`format` together with `tools` in one call.** Ollama does not document the combination, so the fix comes from a call of its own with the schema.
- **A fuzzy join of the halves.** A wrong join duplicates or drops lines that citations point at; review is safer than a silent misjoin.

## Consequences

- Each step can be inspected, replayed and debugged on its own. Repair costs a text call, not a vision call.
- No parse reply grows past about 25 rows; a document of n pages makes at least n parse calls.
- A misread on an image without a text layer is not repaired, only caught where a check sees it.
- A two-column page or wrapped prose may not join after a re-read and keeps LC-02.
- On a laptop CPU a page takes about 1.5-5 minutes to transcribe and a part a few minutes to parse.

## What would change it

Evals comparing one step with two; parts losing items at their cuts; joins that lose or duplicate lines; evidence that a re-read helps in a case the checks can identify ; the eval comparing the two repairs (success rate, tokens and seconds per repair, unsafe errors after repair) decides the default, and the tool repair can become the default only if it is not slower per repair.
