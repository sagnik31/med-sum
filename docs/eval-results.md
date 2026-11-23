# Live eval results: `qwen3.5:4b` and `gemma4:e2b`

The first live eval suite ran on 2026-11-21 and 2026-11-22, on a laptop CPU, over the committed synthetic dataset (25 documents for 6 fictional patients). It compared the project's two local models. Each model ran as the primary, with the other as its fallback, as the service runs them. What each eval measures is in [Development](development.md#evals), and why the evals are built this way is in [ADR 0009](adr/0009-measured-evals.md).

This page gives the headline numbers and what they show. Each run's full report is in [`scripts/evals/reports/`](../scripts/evals/reports/README.md). The overview's judge is on its own page, [Overview judge](eval-judge.md), and what the results recommend is in [Model recommendation](model-recommendation.md) and [ADR 0013](adr/0013-model-choice-after-the-first-live-evals.md).

The code has changed since these runs ([ADR 0013](adr/0013-model-choice-after-the-first-live-evals.md)): two more checks (validators 11 and 12), an overview check for allergies and the latest prescription, no fallback model for the overview, and no auto-verification, so a document that passes every check now ends `checks_passed` and waits for a sign-off. The numbers here are from before those changes; no live run has measured them yet.

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

The MCP client eval was not run. It needs the API, the AI service and the dataset loaded first.

## Headlines

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

## Findings

1. **No model passes the safety gate, so the reports choose none.** Both models are several times over the 1% unsafe-error limit, in the reasoning stage and in the whole pipeline. The headline that matters most is the unsafe errors inside documents that ended `verified`, which no clinician reviewed then (they now wait for a sign-off): 8 of 149 items for `qwen3.5:4b`, 9 of 78 for `gemma4:e2b`. Today the deterministic checks don't catch enough of what these models get wrong for documents to be auto-verified safely.
2. **`gemma4:e2b` mostly doesn't finish as primary.** With `gemma4:e2b` as primary, `qwen3.5:4b` (its fallback) finished 23 of the 25 end-to-end records and 19 of the 25 reasoning records. Its pipeline numbers are therefore mostly `qwen3.5:4b`'s, and they are still worse: 27 unsafe errors against 10.
3. **Both models read well enough.** Both pass the 95% value-recall gate. `qwen3.5:4b` keeps the layout better (97.2% against 86.4%), and `gemma4:e2b` reads about five times faster.
4. **The parse is where the errors come from.** In the reasoning stage both models start from correct transcriptions, and they still make 10 and 11 unsafe errors. A better reading step would not reach the gate on its own.
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
