# 0026. Eval method: measure the safety net, keep the numbers honest

- **Status:** Accepted, 2026-11-09 (scoring rules) and 2026-11-12 (the rest). Built; the live runs over the dataset are not made yet.

## Context

The design rests on "deterministic checks catch what the model gets wrong". That needs measuring, not asserting: what share of wrong values reach a doctor unflagged? Eval numbers are also easy to fool yourself with: prompts that quote eval documents, thresholds finer than the data, no record of what produced a number.

## Decision

- **Fault injection without a model** (`python -m evals.faultcheck`): one fault at a time on each answer key (digit swap, decimal shift, unit swap, a range bound taken for the value, a dropped row or medicine, an invented result or medicine, a moved remark, a changed strength or frequency, a date swap, a wrong patient, a misread in the transcription), then the validators and signals, counting faults that raise a routing signal. It fails when a clean document ends anywhere but where the manifest expects, or when a fault class is caught less often than the committed baseline.
- **Scoring against the keys** (`scripts/src/evals/`): values compare exactly, case-sensitive, ignoring only Unicode forms and spacing. Items pair by name at 90% similarity or more. A wrong value counts as one false positive and one false negative. Derived values are reported apart from printed ones. Transcription is scored for content and layout separately, and for value recall.
- **Model choice, safety first:** a model qualifies only below a limit on unsafe errors (an invented result, medicine or allergy, or a wrong clinical value) and above a transcription value recall; then the higher field F1 wins; speed only breaks a tie.
- **The risk-coverage line:** the share of documents that end `verified` and the unsafe errors inside that share. This is the headline number.
- **Honesty:** every count carries a 95% Wilson interval; dev, test and held-out documents are reported apart; a test checks no prompt shares a line or a quoted example with any transcription.
- **Reproducibility:** each live run's full report and a small history entry (code commit, prompt hashes, model digests, counts) are committed; `evals.compare` fails when unsafe errors rise or routing falls. Model replies are recorded as cassettes keyed by model, request, prompt and input, so a run can be replayed without Ollama, and a changed prompt is a miss that asks for a re-record.

## Alternatives rejected

- **Accuracy alone.** Says nothing about whether errors reach a doctor.
- **Bare percentages.** 24 documents can't support them; 0 unsafe errors in 144 items still allows up to 2.6% at 95%.
- **Results in a gitignored folder.** Nobody could see what the evals looked like over time.

## Consequences

- The checks' blind spots are known by name and count, and the README lists them.
- Gates set as percentages are coarse at this dataset size.

## What would change it

A larger dataset, which would let the gates be stated as rates; clinician review outcomes, which give an escape rate from real use.
