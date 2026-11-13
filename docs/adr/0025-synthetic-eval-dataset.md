# 0025. A synthetic dataset with answer keys, generated and committed

- **Status:** Accepted, 2026-11-07 and 2026-11-08; held-out documents added 2026-11-12. Built.

## Context

The pipeline needs documents to test against, and the evals need answer keys. Real medical documents can't be committed, and documents found online carry their own licences and someone's data.

## Decision

- **Fictional documents in Indian conventions:** English, mg/dL, `1-0-1`, BD/TDS/SOS, brand names, fictional labs, doctors and patients. 24 documents for 6 patients: lab reports (PDFs with and without a text layer, a PNG) and prescriptions (images and a PDF), several per patient across dates, so the summary has something to work across.
- **Clean printed documents first.** Scans, handwriting and noise are a later, harder set.
- The generator (`scripts/src/dataset/`) writes each document and, from the same blocks, its **expected transcription** (numbered lines) and **answer key** in the record format, so the three can't disagree. Content is written out by hand, and rendering is byte-identical on every build. Generator, documents and keys are committed; a test checks the committed files match a fresh build.
- The set contains, on purpose, the cases the first model-written summaries got wrong (a remark near the wrong test, a single creatinine value, a statin with no lipid panel, prescriptions dated after the labs, uploads out of order), an undated document, a document printed for another patient and an unsupported type.
- **Splits:** two documents used for every prompt check are `dev`; seven documents for two further patients, in layouts the others don't use, are `held_out` and never tuned on; the rest are `test`.

## Alternatives rejected

- **Real or public documents.** Privacy and licensing.
- **Random generation with a seed.** Hand-written content makes each case deliberate and each key reviewable.

## Consequences

- The dataset is small: rates carry wide intervals (see [0026](0026-eval-method.md)).
- A gap the held-out documents reveal is fixed only as a general rule with test cases of its own, never as a held-out special case.

## What would change it

A larger or harder set (scans, handwriting) once the clean set is measured, or de-identified real documents under a proper agreement.
