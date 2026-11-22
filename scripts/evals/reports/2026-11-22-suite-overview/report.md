# Overview eval: suite-overview

Generated 2026-11-22T13:21:50+00:00 on dataset med-sum synthetic v2; facts from the answer keys; models: `qwen3.5:4b`, `gemma4:e2b`.

## Code's scores

| Metric | qwen3.5:4b | gemma4:e2b |
| --- | --- | --- |
| Overviews | 6 | 6 |
| Shown (passed the check) | 5/6 (83.3%, 95% CI 43.6%-97.0%) | 1/6 (16.7%, 95% CI 3.0%-56.4%) |
| Rejected after the retry | 1 | 5 |
| Empty | 0 | 0 |
| Model replies | 9 | 12 |
| Attention tests named, shown | 34/34 (100.0%, 95% CI 89.8%-100.0%) | 1/1 (100.0%, 95% CI 20.7%-100.0%) |
| Median time per overview | 47 s | 16 s |

## Judge

Not judged yet: judge.json and judge.md are beside this report; add the verdicts with `python -m evals.overview --verdicts FILE --report DIR`.

## By patient

### `qwen3.5:4b`

| Patient | Outcome | Replies | Attention named | Time |
| --- | --- | --- | --- | --- |
| meera | shown | 1 | 16/16 | 71 s |
| rohan | shown | 1 | 6/6 | 33 s |
| fatima | shown | 2 | 1/1 | 44 s |
| arjun | rejected | 2 | 0/3 | 64 s |
| kabir | shown | 1 | 7/7 | 34 s |
| nandini | shown | 2 | 4/4 | 51 s |

### `gemma4:e2b`

| Patient | Outcome | Replies | Attention named | Time |
| --- | --- | --- | --- | --- |
| meera | rejected | 2 | 0/16 | 40 s |
| rohan | rejected | 2 | 0/6 | 17 s |
| fatima | shown | 2 | 1/1 | 14 s |
| arjun | rejected | 2 | 0/3 | 14 s |
| kabir | rejected | 2 | 0/7 | 17 s |
| nandini | rejected | 2 | 0/4 | 14 s |

