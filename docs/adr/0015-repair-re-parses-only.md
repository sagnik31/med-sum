# 0015. Repair re-parses; it never re-transcribes

- **Status:** Accepted, 2026-11-13 (replacing an earlier plan to re-transcribe pages a finding pointed at). Built.

## Context

When validation finds errors, the model gets another try. The first plan was to re-parse with the findings in the prompt, and to re-transcribe a page when a finding pointed at it. But at temperature 0 the same model reads the same page the same way, and no finding tells a vision misread from a parse error.

## Decision

- **Repair re-parses only.** The previous reply goes back as the assistant's turn, followed by a repair prompt listing each problem (a schema refusal reason, or each error finding with its item, field, page and line).
- **Budget:** the first parse plus up to two repairs per model (`MAX_PARSE_ATTEMPTS = 3`). Still failing, the fallback model gets the same budget, once. Then the document goes on with the last record that validated, and the surviving errors become signals.
- Only **error** findings send a parse back. Flags and info findings never do: re-parsing can't change what the document says.
- Vision misreads reach a clinician through the text-layer check and the unreadable-line signal instead.

## Alternatives rejected

- **Re-transcribe the page a finding points at.** At temperature 0 it returns the same reading, after minutes per page.
- **Unlimited retries.** A document that can't be parsed would loop.

## Consequences

- Repair is cheap: a text call, not a vision call.
- A page misread on an image without a text layer is not repaired, only caught where a check sees it.
- One narrow re-read exists outside repair: a page whose reading hit the output cap is read again in two halves (see [0034](0034-capped-page-read-again-in-halves.md)).

## What would change it

Eval evidence that a re-read helps in a case the checks can identify. A narrow candidate is a page whose transcription was cut off by the output cap, re-read in parts.
