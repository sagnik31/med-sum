# Eval reports

The full report of every live eval run (`python -m evals.run --live`, from `scripts/src`), one folder per run:

- `report.md`: the model choice, an overview with the models side by side, a table per model by case and by document, and every unsafe, missed and invented item.
- `report.json`: the same data, plus each document's record and transcription scores and every field error.

Each folder has the same name as its entry in [`../history/`](../history/), which holds the run's headline counts and what produced it: the code commit (and whether the tree had changes), the prompt hashes and the model digests. `python -m evals.history` lists every run, and `python -m evals.compare A B` compares two of them.

Every document is fictional, so the reports contain no real patient data. The model replies behind each run are in [`../cassettes/`](../cassettes/), so `python -m evals.run --replay` rebuilds a run's report without Ollama, as long as the prompts, pages and options are unchanged.

A replay, or a live run with `--no-history`, only reproduces or tries something and writes to `../results/`, which is not committed.
