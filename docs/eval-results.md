# Live eval results: `qwen3.5:4b` and `gemma4:e2b`

The first live eval suite ran on 2026-11-21 and 2026-11-22, on a laptop CPU, over the committed synthetic dataset (25 documents for 6 fictional patients). It compared the project's two local models. Each model ran as the primary, with the other as its fallback, as the service runs them. What each eval measures is in [Development](development.md#evals), and why the evals are built this way is in [ADR 0009](adr/0009-measured-evals.md).

This page gives the headline numbers and what they show. Each run's full report is in [`scripts/evals/reports/`](../scripts/evals/reports/README.md). The overview's judge is on its own page, [Overview judge](eval-judge.md), and what the results recommend is in [Model recommendation](model-recommendation.md) and [ADR 0013](adr/0013-model-choice-after-the-first-live-evals.md).

The code has changed since these runs ([ADR 0013](adr/0013-model-choice-after-the-first-live-evals.md)): two more checks (validators 11 and 12), an overview check for allergies and the latest prescription, no fallback model for the overview, and no auto-verification, so a document that passes every check now ends `checks_passed` and waits for a sign-off. The end-to-end and injection evals have been measured again under those changes, in [Under the current code](#under-the-current-code). The vision, reasoning, overview and tool-probe numbers are still from before them.

Every count comes with a 95% Wilson interval in its report. With 25 documents, or 6 patients, most intervals are wide, so a gap of a few points between the models is not a difference.

## The runs

| Run | Report | Wall time | What it measures |
|---|---|---|---|
| Vision | [suite-vision](../scripts/evals/reports/2026-11-21-suite-vision/report.md) | ~2.5 h | Reading the pages only |
| Reasoning | [suite-reasoning](../scripts/evals/reports/2026-11-22-suite-reasoning/report.md) | ~10.5 h | The record and its routing, from the expected transcriptions |
| End to end | [suite-e2e](../scripts/evals/reports/2026-11-22-suite-e2e/report.md) | ~9 h 47 min | The whole pipeline |
| Overview | [suite-overview](../scripts/evals/reports/2026-11-22-suite-overview/report.md) | ~6 min | The patient overview, plus the judge |
| Repair (15 core cases) | [suite-repair](../scripts/evals/reports/2026-11-22-suite-repair/report.md) | ~2 h 16 min | Fixing one injected fault: a fixed re-parse against repair with tools |
| Prompt injection | [suite-injection](../scripts/evals/reports/2026-11-22-suite-injection/report.md) | ~2 h | Six attacked documents |
| Tool-calling probe | [toolprobe](../scripts/evals/reports/2026-11-22-toolprobe/probe.json) | ~3 min | Whether a model calls the read-only tools correctly |
| Re-records, 2026-11-27 | none (`--no-history`; the cassettes are committed) | ~5 h 20 min | 12 end-to-end and 1 injection document whose repairs the new checks changed |
| Repair, new fault classes, 2026-11-28 | [repair-new-faults](../scripts/evals/reports/2026-11-28-repair-new-faults/report.md) | ~16 min | One core case per fault class added with validators 11 and 12, `qwen3.5:4b`, fixed re-parse |

The MCP client eval was not run. It needs the API, the AI service and the dataset loaded first.

## Headlines (2026-11-22)

The end-to-end, injection and repair rows have current numbers in [Under the current code](#under-the-current-code).

| Measure | `qwen3.5:4b` | `gemma4:e2b` |
|---|---|---|
| **Vision:** transcription value recall (gate: 95%) | 1443/1445 (99.9%) | 1420/1445 (98.3%) |
| Vision: layout kept | 601/618 (97.2%) | 515/596 (86.4%) |
| Vision: median time per document | 232 s | 45 s |
| **Reasoning:** unsafe-error rate (gate: 1%) | 10/204 (4.90%) | 11/204 (5.39%) |
| Reasoning: field F1 | 91.1% | 91.4% |
| Reasoning: records finished by the fallback | 3 | 19 |
| **End to end:** unsafe-error rate (gate: 1%) | 10/204 (4.90%) | 27/204 (13.24%) |
| End to end: field F1 | 91.4% | 89.3% |
| End to end: documents auto-verified | 18/25 (72%) | 12/25 (48%) |
| End to end: items with an unsafe error inside auto-verified documents | 8/149 (5.4%) | 9/78 (11.5%) |
| End to end: records finished by the fallback | 2 | 23 |
| **Overview:** shown (passed the check) | 5/6 | 1/6 |
| **Repair:** cases repaired, fixed re-parse / with tools | 14/15 / 15/15 | 14/15 / 13/15 |
| Repair: unsafe errors after repair, fixed / with tools (13 before) | 1 / 0 | 2 / 2 |
| **Injection:** faithful to the clean document | 5/6 | 2/6 |
| Injection: followed the attack | 0/6 | 1/6 |
| Injection: unsafe errors | 1 | 6 |
| Injection: page contained (no live markup) | 6/6 | 6/6 |
| **Tool probe:** cases passed | 6/7 | 7/7 |
| Tool probe: total time | 159 s | 24 s |

## Under the current code

The stricter checks changed what a repair is sent for 12 of the 50 end-to-end documents and 1 of the 12 injection ones, so their recordings stopped replaying. They were recorded again live on 2026-11-27, against the same model builds, with the vision and reasoning replies reused where the requests matched. Every other document's requests are unchanged, so its recorded replies are what the current code gets from the model. A replay of every recording (`python -m evals.run --replay`, `python -m evals.injection --replay`, from commit `69b7269` on) therefore runs the current checks and routing over the whole set. These are its numbers. They are a replay, not a new history entry: the committed history and CI's baseline are still the 2026-11-22 run.

| End to end | `qwen3.5:4b`, 2026-11-22 → now | `gemma4:e2b`, 2026-11-22 → now |
|---|---|---|
| **Unsafe-error rate (gate: 1%)** | 10/204 (4.90%) → **3/204 (1.47%, 95% CI 0.5%-4.2%)** | 27/204 (13.24%) → **14/204 (6.86%, 95% CI 4.1%-11.2%)** |
| Field F1 | 91.4% → 94.0% | 89.3% → 92.0% |
| Medicine recall | 80.0% → 96.7% | 86.7% → 96.7% |
| Invented items | 18 → 11 | 31 → 19 |
| Clinical values present (gate: 95%) | not reported → 98.7% | not reported → 98.4% |
| Documents that passed every check (now `checks_passed`) | 18/25 → 18/25 | 12/25 → 12/25 |
| Items with an unsafe error inside those documents | 8/149 (5.4%) → **1/143 (0.7%, 95% CI 0.1%-3.9%)** | 9/78 (11.5%) → 4/74 (5.4%, 95% CI 2.1%-13.1%) |
| End state as the manifest expects | 21/25 → 21/25 | 15/25 → 15/25 |
| Records the other model finished as fallback | 2 → 2 | 23 → 23 |

The injection eval replays to the same numbers as on 2026-11-22: faithful 5/6 and 2/6, attacks followed 0/6 and 1/6, unsafe errors 1 and 6, every page contained.

The repair eval's 15 recorded core cases also replay to the same numbers. On 2026-11-28 one core case for each fault class added with validators 11 and 12 was recorded, on `qwen3.5:4b` with the fixed re-parse ([repair-new-faults](../scripts/evals/reports/2026-11-28-repair-new-faults/report.md)): 4/5 repaired (95% CI 38%-96%), unsafe errors 5 before and 1 after. The strength left in the name, the frequency given as the dose, the cut allergy text and the flag left in the value were each repaired on the first repair. The header row read as a result was not: two re-parses raised its errors from 1 to 10, so the record stays flagged and goes to review. With one case per class, how typical that is isn't known.

## Findings

Findings 1 and 2 have the 2026-11-22 numbers and the current ones; the rest are from 2026-11-22.

1. **No model passes the safety gate, so the reports choose none.** On 2026-11-22 both models were several times over the 1% unsafe-error limit, in the reasoning stage and in the whole pipeline, and documents that ended `verified`, which no clinician reviewed then, held unsafe errors in 8 of 149 items for `qwen3.5:4b` and 9 of 78 for `gemma4:e2b`. With validators 11 and 12, `qwen3.5:4b` is at 3 of 204 (1.47%), close to the gate but above it, and its documents that pass every check hold 1 unsafe item in 143. `gemma4:e2b` is at 14 of 204. The interval on 3 of 204 runs from 0.5% to 4.2%, so 25 documents can't show that `qwen3.5:4b` is under 1% even if it is. Documents still wait for a sign-off.
2. **`gemma4:e2b` mostly doesn't finish as primary.** With `gemma4:e2b` as primary, `qwen3.5:4b` (its fallback) finished 23 of the 25 end-to-end records and 19 of the 25 reasoning records. Its pipeline numbers are therefore mostly `qwen3.5:4b`'s, and they are still worse: 27 unsafe errors against 10 on 2026-11-22, and 14 against 3 now.
3. **Both models read well enough.** Both pass the 95% value-recall gate. `qwen3.5:4b` keeps the layout better (97.2% against 86.4%), and `gemma4:e2b` reads about five times faster.
4. **The parse is where the errors come from.** In the reasoning stage both models start from correct transcriptions, and they still make 10 and 11 unsafe errors. A better reading step would not reach the gate on its own. (The reasoning stage hasn't been measured under the new checks: some of its recordings no longer replay.)
5. **`gemma4:e2b` rarely writes an overview the check accepts.** The check rejected 5 of its 6 overviews, even after a retry. It rejected 1 of `qwen3.5:4b`'s 6.
6. **Repair works for both, and tools help only `qwen3.5:4b`.** With tools, `qwen3.5:4b` repaired all 15 cases and left no unsafe error, at three times the tokens and about 1.5 times the time. `gemma4:e2b` made no tool calls at all in the tools variant, though it called them correctly in the tool probe. The report supports the fixed re-parse as the default for both.
7. **`qwen3.5:4b` resists prompt injection better.** Neither model let an attack's payload into a built page. `gemma4:e2b` followed one attack (an added medicine) and made 6 unsafe errors over the attacked documents, against `qwen3.5:4b`'s 1.
8. **`gemma4:e2b` is the better tool caller in the probe.** It passed all 7 cases, about seven times faster. `qwen3.5:4b` called a tool when none was needed.

## Report oddities

These were seen during the suite and left as reported, since no code changed during it:

- The vision report shows 0 in every document-count column, although all 25 documents were scored.
- The reasoning and end-to-end reports count unsafe errors over 204 clinical items in the model choice, and over 236 or 245 items in the overview table.
- History entries say `dirty: true`, while the resume fingerprint saw no change outside the eval outputs.
- The end-to-end stage called the models on most documents, even ones the vision run read exactly.
- The repair and injection evals write no history entry, and the tool probe writes only to the gitignored `results/`. Its output was copied into the reports folder.
