# 0005. Local models with a fallback and a circuit breaker; cloud only for synthetic text

- **Status:** Accepted, 2026-11-07 and 2026-11-09; cloud guard 2026-11-13. Built. No cloud provider is configured; whether to add one is decided later.

## Context

Patient documents shouldn't leave the hospital's machines, and the project uses no paid infrastructure. Local 4B-parameter models on a laptop CPU take minutes per page, can error, time out or hit their output cap, and a dead model shouldn't cost a 10-minute timeout on every call.

## Decision

- **All inference is local**, through Ollama, with exactly two models: `qwen3.5:4b` (primary, vision and text) and `gemma4:e2b` (fallback). Which is primary is for the evals to decide. Calls are as deterministic as Ollama allows: temperature 0, thinking off, fixed context size, an output cap per call, Ollama's `stop` option for the end marker.
- **Every model call goes through `scripts/src/llm.py`.** An error, a timeout (nothing for 600 seconds) or a capped reply on the primary sends the same request to the fallback. Cut-off output is never saved as a result.
- **Fallback use is operational, not low confidence:** logged, kept in provenance (the model role) and shown in review, while the checks judge the output on its merits.
- **A circuit breaker per model** (`scripts/src/model_health.py`), in memory: 3 consecutive infrastructure failures (errors, timeouts) skip the model for 15 minutes, then one probe call decides. Quality failures (off-schema, capped, failing validation) are counted separately and never trip it. With every model skipped, a call fails at once with "No model available". State is on `GET /health`.
- **A cloud model is refused unless the documents are marked synthetic**, and never sent a page image whatever the marking: a page prints the patient's name, and an image can't be masked. A cloud model is one named like a cloud provider's, an Ollama cloud tag, or one listed in `MEDSUM_CLOUD_MODELS`. A refused model counts as unavailable. Any cloud option would be a free tier for faster testing on synthetic data, never paid.

## Alternatives rejected

- **A cloud model as the main path.** Patient data leaves the machine, and it costs money.
- **Larger local models.** Too slow or too large for the target CPU.
- **Counting bad output toward the breaker.** One hard document would switch off a healthy model for everyone.
- **Treating fallback output as low confidence.** Every fallback document would go to review even when its values check out.
- **Trusting callers to keep data off cloud models.** The guard sits where every call passes.

## Consequences

- Everything runs on one machine with no account or key; a full dataset run takes hours.
- A down model costs three failed calls, not a timeout per document. The breaker lives in one process and forgets on restart.

## What would change it

Faster local hardware; the evals showing neither local model passes the safety gate; several service processes, which would need shared breaker state; real data for a cloud model, which would need pseudonymised text.
