# Eval reports

The full report of every live eval run (`python -m evals.run --live`, from `scripts/src`), one folder per run. A run of one stage (`--stage vision` or `--stage reasoning`) is named for its stage (`suite-vision`, `suite-reasoning`); the whole pipeline is `suite-e2e`. Each folder holds:

- `report.md`: the model choice, an overview with the models side by side, a table per model by case and by document, and every unsafe, missed and invented item.
- `report.json`: the same data, plus each document's record and transcription scores and every field error.

Each folder has the same name as its entry in [`../history/`](../history/), which holds the run's headline counts and what produced it: the code commit (and whether the tree had changes), the prompt hashes and the model digests. `python -m evals.history` lists every run, and `python -m evals.compare A B` compares two of them.

Every document is fictional, so the reports contain no real patient data. The model replies behind each run are in [`../cassettes/`](../cassettes/), so `python -m evals.run --replay` rebuilds a run's report without Ollama, as long as the prompts, pages and options are unchanged.

A replay, or a live run with `--no-history`, only reproduces or tries something and writes to `../results/`, which is not committed.

A repair eval run (`python -m evals.repair --live`) is kept here too, with its own `report.md` and `report.json` (rows per model and repair, by fault, and every case). It has no history entry: its rows are compared within the report, fixed re-parse against tool repair. Its cassettes are under [`../cassettes/repair/`](../cassettes/repair/).

An overview eval run (`python -m evals.overview --live`) also keeps what its judge needs and wrote: `judge.json` (each shown overview beside its facts), `judge.md` (the eval's rubric), `rubric.md` (an extended rubric with severities and gates, fixed before judging) and `verdicts.json` (the judge's verdicts, whose counts `--verdicts` added to `report.md`). What the first judge found is on [Overview judge](../../../docs/eval-judge.md), and the whole suite's results on [Live eval results](../../../docs/eval-results.md).

A prompt-injection run (`python -m evals.injection --live`) has a folder of its own too, with no history entry. The tool-calling probe (`python -m evals.toolprobe --live`) writes only to `../results/`; the first one's `probe.json` was copied into `2026-11-22-toolprobe/`.

[`SUITE-PROGRESS.md`](SUITE-PROGRESS.md) holds the first suite's progress notes: the order of the runs, their timings and what was seen along the way.

These runs, and the replies behind them, came before [ADR 0013](../../../docs/adr/0013-model-choice-after-the-first-live-evals.md)'s changes, and some of the replies no longer match their requests: see [Limitations](../../../docs/limitations.md).
