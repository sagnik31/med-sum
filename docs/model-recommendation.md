# Model recommendation after the first live evals

This page gives the recommendations of `claude-opus-5-5`, which judged the overview eval and reviewed the whole suite. The question was which of the two local models should be primary, and whether a better model is needed. The evidence is on [Live eval results](eval-results.md) and [Overview judge](eval-judge.md). The owner accepted them on 2026-11-23, and the decision is recorded in [ADR 0013](adr/0013-model-choice-after-the-first-live-evals.md).

These are recommendations, not measurements. Each one names the numbers it rests on, and most of those numbers come from 25 documents or 6 patients.

## In short

- **`qwen3.5:4b` should be primary** for every step.
- **`gemma4:e2b` should stay the fallback** for reading and parsing, so the service keeps working when `qwen3.5:4b` is down. It should not write the overview.
- **Neither model is safe enough for documents to be auto-verified.** A better parse is needed: better checks first, then a larger local model for parsing if the checks can't close the gap.

## Recommendations

### 1. `qwen3.5:4b` as primary for every step

Every eval that counts unsafe errors points the same way:

| Evidence | `qwen3.5:4b` | `gemma4:e2b` |
|---|---|---|
| Unsafe errors, whole pipeline | 10/204 | 27/204 |
| Records finished by the other model, as fallback | 2/25 | 23/25 |
| Overviews shown by the check | 5/6 | 1/6 |
| Overview judge's outcome | primary (5 patients) | not usable (1 patient) |
| Injection: followed the attack / unsafe errors | 0/6 / 1 | 1/6 / 6 |
| Repair with tools: cases repaired / unsafe errors after | 15/15 / 0 | 13/15 / 2 |

`gemma4:e2b` is faster: about five times on reading and seven times in the tool probe. It also did better in the tool probe, 7/7 against 6/7. Speed only breaks a tie under the rules in [ADR 0009](adr/0009-measured-evals.md), though, and there is no tie on safety. This keeps the configuration the service already has.

### 2. `gemma4:e2b` as the fallback for reading and parsing only

A fallback is there so a down primary doesn't stop the service. When `gemma4:e2b` reads or parses, the same checks and the same routing judge its output, and a clinician reviews what they flag. That keeps its errors visible. The overview is different: it is free text that a clinician reads as it is, and the one `gemma4:e2b` overview the check showed left out a recorded penicillin allergy. When `qwen3.5:4b` can't write the overview, the summary page should show no overview rather than `gemma4:e2b`'s. The page is built by code either way, so it still holds every value.

### 3. No auto-verification until a model and the checks pass the safety gate

Both models make unsafe errors several times over the 1% limit. `qwen3.5:4b`'s documents that end `verified`, which no clinician reviews, still hold unsafe errors in 8 of 149 items (5.4%, 95% CI 2.7%-10.2%). Until a run passes the gate, every document should reach a clinician. A document whose checks pass could still be marked as such for the reviewer, but it shouldn't skip review.

### 4. A better parse is needed, and the checks come first

Even from correct transcriptions, the reasoning stage still makes 10 and 11 unsafe errors, so the errors come from parsing, not reading. Two ways to close the gap, in this order:

1. **Catch more of the errors with the deterministic checks.** Go through `qwen3.5:4b`'s 10 unsafe errors by kind. For each kind a check could catch, add the check and a fault-check case. This needs no new model, keeps "code decides", and the fault check's baseline measures it.
2. **If the checks can't close the gap, evaluate a larger local model for the parse step only.** [ADR 0005](adr/0005-local-models-with-fallback.md) rejected larger models as too slow or too large for the CPU. A parse model reads only text, so it needs no vision support. Whether it is fast enough is for an eval to measure: `qwen3.5:4b` already takes a median of 321 s per document in the reasoning stage. ADR 0005 also names "the evals showing neither local model passes the safety gate" as a reason to revisit it, and this run is that.

### 5. The fixed re-parse as the default repair

The repair report supports the fixed re-parse for both models. Tools let `qwen3.5:4b` repair every case and leave no unsafe error, but at three times the tokens and 1.5 times the time. `gemma4:e2b` didn't use the tools at all. Repair with tools is worth re-measuring for `qwen3.5:4b` on the full repair set, but it shouldn't be the default on 15 cases.

### 6. Fixes the overview judge found

- **The overview check should require a recorded drug allergy, and the latest prescription, to be named.** Today it tests only that what is said is right. These two are the omissions the rubric calls critical.
- **The facts should say "latest flagged value" with its date, not "Flagged in the latest report".** The wording is wrong for values older than the latest report, and the model reads the same wording.
- **The judge's table in the report should show omissions by severity.** As the table stands, 3 minor omissions look worse than 1 critical one.

These change the overview's prompt input or its check, so the overview eval should be run again after them. It takes about 6 minutes.

### 7. Making the next judging stronger

- Give the judge opaque item ids, so it doesn't know which model wrote what.
- Re-judge a few items, and have a person check a sample, then report how often they agree.
- Run `gemma4:e2b` on more patients only if it is still a candidate for writing the overview.
