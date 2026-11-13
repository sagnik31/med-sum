# 0004. Two model steps: transcribe, then parse

- **Status:** Accepted, 2026-11-08. Built.

## Context

A vision-language model can turn a page image straight into JSON. But the validators need something to check extracted values against, and a one-step image-to-JSON call leaves nothing: the image is the only source, and no code can read it.

## Decision

Each document goes through two model steps:

1. **Transcribe.** Each page image goes to the vision model with one generic prompt, and comes back as numbered lines of text (Markdown tables kept). Every document goes through this path, even a PDF with a text layer, so a scan and a digital PDF behave the same way.
2. **Parse.** The text-only model call fills in the schema for the document's type from the numbered transcription, using Ollama's JSON-schema `format`. Every item cites the page and lines it came from.

The transcription is the anchor for every citation and the text the validators check against. A PDF's own text layer, where it has one, is read alongside and used only as an independent check (see [0012](0012-pdf-text-layer-check.md)).

## Alternatives rejected

- **One step, image to JSON.** Nothing to check values against; misreads happen either way, and one step only hides them. Small models also tend to read images worse when they must produce a strict schema at the same time.
- **The PDF text layer as the main input.** Many documents are scans or photos with no text layer, and table layout in a text layer is often scrambled. Using it only as a check keeps one path for every document.

## Consequences

- Each step can be inspected and debugged on its own; both are stored as steps of the run.
- Repair only re-runs the cheap text parse, not a vision call of minutes per page (see [0015](0015-repair-re-parses-only.md)).
- The parse step is close to copying, and the validators catch anything it changes or invents. A misread in the vision step is caught only where a text layer exists.
- Two calls per document cost more time than one; on a laptop CPU a page takes about 1.5-5 minutes to transcribe and a parse about 4 minutes.

## What would change it

An eval run comparing one step with two on the same documents, showing one step at least as accurate with no loss of checkability.
