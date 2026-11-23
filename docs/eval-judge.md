# Overview judge: `claude-opus-5-5`

The patient overview is the one place a model writes free text that a clinician reads. A deterministic check decides whether an overview is shown, but it compares words and numbers, not meanings ([ADR 0007](adr/0007-pages-built-by-code.md)). So the overview eval also has a judge outside the service, a stronger model that checks each shown overview against the facts it was written from ([ADR 0009](adr/0009-measured-evals.md)). This page is the judge's report on the first live run. The other evals are on [Live eval results](eval-results.md).

The files are in [`scripts/evals/reports/2026-11-22-suite-overview/`](../scripts/evals/reports/2026-11-22-suite-overview/):

| File | What it is |
|---|---|
| `judge.json` | The items judged: each shown overview with the facts code built for it |
| `judge.md` | The eval's own rubric |
| `rubric.md` | The extended rubric: error types, severities, gates and outcomes |
| `verdicts.json` | The judge's verdicts, one per item |
| `report.md` | The run's report, with the verdicts' counts added by `python -m evals.overview --verdicts` |

## How it was judged

- **The rubric was written and its thresholds fixed before any item was judged.** `rubric.md` extends `judge.md`. Each wrong sentence gets an error type and a severity: critical, major or minor. Omissions are counted by group of results, not by value, and graded the same way. Two gates are safety gates (no misleading overview, no critical error, no critical omission) and two are quality gates (at most one major error, and one major omission, in any overview). Passing all of them makes a model **primary**, passing only the safety gates makes it **fallback**, and failing a safety gate makes it **not usable**.
- **The judge read only the items and the rubric.** It didn't see the code, the answer keys, the eval's report or the source documents, and it judged each overview against its facts only, never against medical knowledge.
- **The judge labels and the counts decide.** The judge gives no score and no ranking. The outcomes follow from the counts and the gates.
- **The judge's verdicts don't gate anything in code.** What is shown is still the deterministic check's decision. The gates informed the model choice, which the owner accepted in [ADR 0013](adr/0013-model-choice-after-the-first-live-evals.md).

## Verdicts

Only overviews the check showed were judged: 5 by `qwen3.5:4b` and 1 by `gemma4:e2b`. The check rejected the rest, and those reached no clinician.

| Item | Sentences | Supported | Omissions | Misleading |
|---|---|---|---|---|
| `qwen3.5:4b` meera | 4 | 4 | 1 minor: the no-known-allergies record | no |
| `qwen3.5:4b` rohan | 3 | 3 | 0 | no |
| `qwen3.5:4b` fatima | 3 | 3 | 0 | no |
| `qwen3.5:4b` kabir | 3 | 3 | 1 minor: the no-known-allergies record | no |
| `qwen3.5:4b` nandini | 2 | 2 | 1 minor: which way the trends go | no |
| `gemma4:e2b` fatima | 5 | 5 | **1 critical: the recorded penicillin allergy** | no |

## Outcomes

| Model | Patients judged | Safety gates | Quality gates | Outcome |
|---|---|---|---|---|
| `qwen3.5:4b` | 5 | pass | pass | **Primary** |
| `gemma4:e2b` | 1 | fails: a critical omission | pass | **Not usable** |

So no model qualifies as the overview's fallback. `gemma4:e2b`'s outcome rests on one overview. It is the only patient both models wrote for, though, and on that patient `qwen3.5:4b` named the allergy and `gemma4:e2b` didn't.

## Findings

1. **No overview said anything false.** All 20 sentences follow from their facts. Neither model invented a value, a flag, a trend, a medicine or advice.
2. **The overview check showed an overview that left out a drug allergy.** `gemma4:e2b`'s fatima overview passed the check without the penicillin allergy that two documents record. The check tests that what is said is right, not that what is needed is said, so it can't catch this.
3. **A count of omissions hides their severity.** In `report.md`, the judge's table gives `qwen3.5:4b` 3 omissions and `gemma4:e2b` 1, which reads as though `gemma4:e2b` did better. All three of `qwen3.5:4b`'s are minor, and `gemma4:e2b`'s one is critical. The table now counts omissions and unsupported sentences by severity, from each omission's "[critical]", "[major]" or "[minor]" prefix and each sentence's `severity` field (2026-11-23); it shows `gemma4:e2b`'s one critical omission against `qwen3.5:4b`'s three minor ones.
4. **`qwen3.5:4b` passes largely by copying.** Its overviews list the facts in their order, and they often leave out whether a value is low or high, or which way a trend goes. That keeps them accurate, but they summarise little. The gates don't measure this.
5. **The facts mislabel older values.** In 4 of 6 items, lines headed "Flagged in the latest report" carry a date earlier than the latest lab report. They are the latest value of that test, not values from the latest report. The rubric reads them by their date, and each verdict lists them under `fact_issues`. The wording comes from the code that builds the facts, and the same wording reaches the model that writes the overview.

## Limits

- **Small sample.** Six overviews in all, and one for `gemma4:e2b`. Every outcome is reported with the number of patients it rests on.
- **The judge wasn't blind.** The item ids name the model. Future packets should give the judge opaque ids and map them back afterwards.
- **One judge, one pass.** There is no second judge, re-judging or human spot check yet, so how often other judges would agree is unmeasured.
- **The judge also wrote the rubric.** It wrote the rubric in the same session, and the owner approved the thresholds before judging began. While writing it, the judge had seen the item ids and the first patient's facts, but none of the overviews.
