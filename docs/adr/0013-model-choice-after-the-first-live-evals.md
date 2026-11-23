# 0013. `qwen3.5:4b` stays primary; `gemma4:e2b` falls back for reading and parsing only; no auto-verification until the safety gate passes

- **Status:** Proposed, 2026-11-23, from the recommendations of the overview judge (`claude-opus-5-5`) after the first live eval suite. The owner hasn't accepted it yet, and nothing in it is built. Configuration is unchanged: `qwen3.5:4b` primary and `gemma4:e2b` fallback, as [ADR 0005](0005-local-models-with-fallback.md) set.

## Context

[ADR 0005](0005-local-models-with-fallback.md) left it to the evals to decide which model is primary. The first live suite ran on 2026-11-21 and 2026-11-22: vision, reasoning, end to end, overview, repair, prompt injection and a tool-calling probe, over 25 synthetic documents for 6 patients. Its results are on [Live eval results](../eval-results.md) and the overview judge's on [Overview judge](../eval-judge.md).

- No model passes the 1% unsafe-error gate. In the whole pipeline, `qwen3.5:4b` makes unsafe errors in 10 of 204 clinical items and `gemma4:e2b` in 27. Inside the documents that end `verified`, `qwen3.5:4b`'s items still carry unsafe errors in 8 of 149.
- From correct transcriptions, the reasoning stage still makes 10 and 11 unsafe errors. The parse is where the errors come from.
- With `gemma4:e2b` as primary, `qwen3.5:4b` (its fallback) finished 23 of the 25 end-to-end records.
- `qwen3.5:4b` follows no prompt-injection attack (`gemma4:e2b` follows 1 of 6), and repairs every core case with tools.
- The judge found no false sentence in any shown overview. `gemma4:e2b`'s one shown overview left out a recorded penicillin allergy, so under the rubric it is not usable even as the overview's fallback. `qwen3.5:4b`, on 5 patients, qualifies as primary.

## Decision

- **`qwen3.5:4b` is primary for every step:** reading, parsing, the overview and repair.
- **`gemma4:e2b` is the fallback for reading and parsing only.** Its output goes through the same checks and routing. For the overview there is no fallback: when the primary can't write it, the summary is shown without one.
- **No document ends `verified` without a clinician** until one eval run passes the safety gate on both the model and the checks. Passing checks are still shown to the reviewer.
- **Closing the gap starts with the checks.** First the deterministic checks are extended to catch the kinds of unsafe error `qwen3.5:4b` makes. If they can't close the gap, a larger local model is evaluated for the parse step only, which would amend ADR 0005.
- **The fixed re-parse stays the default repair**, as the repair eval supports for both models.
- **The overview check requires a recorded drug allergy and the latest prescription to be named**, and the overview's facts give older flagged values as "latest flagged value" with their date.

The rubric's gates (primary, fallback, not usable) inform this decision. They gate nothing in code. That keeps ADR 0009's rule that a judge's verdicts are reported, never gated on.

## Alternatives rejected

- **`gemma4:e2b` as primary.** It is faster, but with it as primary the fallback finished almost every record, and the records still had more unsafe errors. Under ADR 0009, speed only breaks a tie on safety, and there is none here.
- **Choosing no model until one passes the gate.** The service needs a primary to run at all. Holding every document for review makes running it safe meanwhile.
- **Keeping `gemma4:e2b` as the overview's fallback.** The overview is the only free text a clinician reads as written, and its one shown overview left out an allergy. No overview is safer than an incomplete one, because the code-built page still holds every value.
- **A larger model straight away.** It costs CPU time on every document, and it needs an amendment to ADR 0005. Checks are cheap, deterministic and measured by the fault check, so they come first.
- **Gating the model choice on the judge in code.** ADR 0009 rejects that: the gate would move with the judge rather than the model.

## Consequences

- The service keeps its configuration, so nothing changes until the parts of this decision are built.
- With no auto-verification, every document costs a clinician's review. Routing still tells them what to look at first.
- The evidence is small: 25 documents, 6 overviews, and 1 shown overview for `gemma4:e2b`. Every count is reported with its interval or its sample size.
- A run after each change measures it: the fault check for new checks, the overview eval (about 6 minutes) for the overview changes, and a full suite for a new parse model.

## What would change it

An eval run in which a model and the checks pass the 1% unsafe-error gate, which would bring auto-verification back. `gemma4:e2b` overviews that pass the judge's safety gates over all six patients. A larger local parse model that passes the gate in acceptable time. A second judge or a human spot check that disagrees with these verdicts.
