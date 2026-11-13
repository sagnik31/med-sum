# 0005. Two document types, classified by the model with a keyword check

- **Status:** Accepted, 2026-11-07 (types) and 2026-11-09 (the combining rule). Built.

## Context

Lab reports and prescriptions are the commonest documents a doctor reads before a visit, and both are mostly structured. Other types (discharge summaries, imaging reports) need their own schemas and checks. The parse step needs the right schema, so the type has to be known before parsing, and a 4B model's label alone can be wrong.

## Decision

- **Scope:** lab reports and prescriptions are parsed. Anything else is classified `other`, keeps its transcription, and ends in review unparsed. The record's primitives are still designed so that other types fit later without a redesign (see [0010](0010-record-as-versioned-jsonb.md)).
- **Classification:** after transcription, the model picks a label from a fixed list (`lab_report`, `prescription`, `other`) with a JSON schema, reading the first 6,000 characters. A keyword check backs it up. Keywords name a type when they find at least 2 signals for it and at least twice as many as for the other type; otherwise they have no opinion.
- **Combining rule:** the model's label wins when the keywords agree or have no opinion. The keywords win when the model gave no usable label or said `other`. When the model says lab report and the keywords say prescription (or the other way round), the document is `other`, and the disagreement is a review signal (LC-15), because the wrong schema would be used.

## Alternatives rejected

- **Keywords alone.** Brittle on layouts they weren't written for.
- **The model alone.** No second opinion; a wrong label silently sends the document through the wrong schema.
- **Asking the model to rate its confidence.** A small model's self-rating is not calibrated.

## Consequences

- A disagreement never guesses: the document goes to a clinician with its transcription.
- The step stores which rule decided, the model's label and the keyword signals.

## What would change it

New document types (each with its schema, checks and keywords), or eval results showing the keyword check overriding correct labels.
