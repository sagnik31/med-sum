# 0009. Evals measure the safety net, on a committed synthetic dataset, with honest uncertainty

- **Status:** Accepted, 2026-11-07 to 2026-11-12. Built; the live runs over the dataset on both models are not made yet.

## Context

The design rests on "deterministic checks catch what the model gets wrong". That needs measuring: what share of wrong values reach a doctor unflagged? Real documents can't be committed, and eval numbers are easy to fool yourself with: prompts that quote eval documents, thresholds finer than the data, no record of what produced a number.

## Decision

**A synthetic dataset, generated and committed** (`scripts/src/dataset/`, output in `scripts/evals/dataset/`). Fictional documents in Indian conventions (mg/dL, `1-0-1`, BD/TDS/SOS, brand names): lab reports as PDFs with and without a text layer and as images, prescriptions as images and PDF, several per patient across dates. Clean printed documents first; scans and handwriting are a later set. The generator writes each document, its expected transcription and its answer key from the same blocks, so they can't disagree; content is written by hand, rendering is byte-identical, and a test checks the committed files match a fresh build. The set holds, on purpose, the cases the first summaries got wrong, an undated document, one printed for another patient and an unsupported type. **Splits:** the documents used for prompt checks are `dev`; documents for two further patients in layouts the others don't use are `held_out` and never tuned on (a gap they reveal is fixed only as a general rule with test cases of its own); the rest are `test`.

**Fault injection without a model** (`python -m evals.faultcheck`): one fault at a time on each key (digit swap, decimal shift, unit swap, a range bound taken for the value, a dropped row or medicine, an invented item, a moved remark, a changed strength or frequency, a date swap, a wrong patient, a transcription misread), then the checks and signals. It fails when a clean document ends anywhere the manifest doesn't expect, or a fault class is caught less often than the committed baseline.

**Scoring** (`scripts/src/evals/`): values exact and case-sensitive, ignoring only Unicode forms and spacing; items paired by name at 90% similarity; a wrong value is one false positive and one false negative; derived values reported apart; transcription scored for content, layout and value recall.

**Model choice, safety first:** a model qualifies only below a limit of unsafe errors (an invented result, medicine or allergy, or a wrong clinical value) and above a transcription value recall; then the higher field F1 wins; speed only breaks a tie. **The headline is the risk-coverage line:** the share of documents ending `verified` and the unsafe errors inside that share.

**Honesty and history:** every count carries a 95% Wilson interval; splits are reported apart; a test checks no prompt shares a line or quoted example with any transcription. Each live run's full report and a small history entry (commit, prompt hashes, model digests, counts) are committed; `evals.compare` fails when unsafe errors rise or routing falls. Model replies are recorded as cassettes keyed by model, request, prompt and input, so a run replays without Ollama and a changed prompt is a miss that asks for a re-record.

## Alternatives rejected

- **Real or public documents.** Privacy and licensing.
- **Accuracy alone.** Says nothing about whether errors reach a doctor.
- **Bare percentages.** At this size, 0 unsafe errors in about 200 items still allows nearly 2% at 95%.
- **Results in a gitignored folder.** Nobody could see how the numbers moved.

## Consequences

- The checks' blind spots are known by name and count, and the README lists them.
- The dataset is small, so gates set as percentages are coarse.

## What would change it

A larger or harder dataset, which would let gates be stated as rates; clinician review outcomes, which give an escape rate from real use.
