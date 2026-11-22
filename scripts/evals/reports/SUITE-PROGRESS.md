# Live eval suite: progress

Started 2026-11-22 from commit d2004c3 (overview eval). Each run is
committed with its report, history entry, cassettes and log once it ends.
A run that stops is continued with `--resume <label>`.

Nothing is committed while a run is in flight: a resume compares HEAD
with the commit the run started from (evals/progress.py), so a commit made
mid-run would get the resume refused. Until a run ends, its progress lives
in scripts/evals/results/<label>/progress/ and its cassettes, which
survive a crash or a reboot.

| # | Run | Label | Status | Wall time | Commit |
|---|---|---|---|---|---|
| 1 | vision | suite-vision | done, exit 0 | ~2.5 h | 0e12b0a |
| 2 | reasoning | suite-reasoning | done, exit 0 | ~10.5 h (04:36-15:04) | see git log |
| 3 | e2e | suite-e2e | pending | | |
| 4 | overview | suite-overview | pending | | |
| - | judge verdicts | | pending | | |
| 5 | repair (core) | suite-repair | pending | | |
| 6 | injection | suite-injection | pending | | |
| 7 | toolprobe | | pending | | |
| 8 | mcp_eval | | skipped by the owner | | |

## Notes

- suite-vision: both models pass the 95% value-recall gate (qwen 99.9%,
  gemma 98.3%). The report shows 0 in every document-count column,
  although all 25 documents were scored. On qwen's d01 the 4096-token cap
  was hit and the fallback answered.
- suite-reasoning: qwen's 25 documents ran 04:36-09:19 (d25 alone took
  1 h 41 min); gemma's d01-d24 ran 09:19-12:43 and its d25 took 8466 s,
  mostly falling back to qwen. No model chosen: both fail the 1% unsafe-error
  gate (qwen 10/204 clinical items, gemma 11/204). Field F1 qwen 91.1%,
  gemma 91.4%. The report gives the unsafe rate over 204 clinical items in
  one place and over 236 items in another; left as reported.
- Run logs are copied into each run's report directory as run.log.
