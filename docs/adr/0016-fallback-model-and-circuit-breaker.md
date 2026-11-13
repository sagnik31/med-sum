# 0016. A fallback model, and a circuit breaker that counts only infrastructure failures

- **Status:** Accepted, 2026-11-09. Built.

## Context

The pipeline runs on two small local models through Ollama: `qwen3.5:4b` as primary and `gemma4:e2b` as fallback. Either can error, time out or hit its output cap. A dead model shouldn't cost a 10-minute timeout on every call, and switching models shouldn't be mistaken for a quality problem.

## Decision

- **All model calls go through `scripts/src/llm.py`.** An error, a timeout (no output for 600 seconds) or a capped reply on the primary sends the same request to the fallback. Cut-off output is never saved as a result. A parse that still fails validation after the primary's repairs gets the fallback's own repair budget (see [0015](0015-repair-re-parses-only.md)).
- **Fallback use is operational, not low confidence.** It is logged, counted, kept in provenance (the model role) and shown in review (OP-1), while the validators judge the output on its merits.
- **A circuit breaker per model** (`scripts/src/model_health.py`), in memory: after 3 consecutive infrastructure failures (errors and timeouts only) the model is skipped for 15 minutes, then one probe call decides whether it is restored. Both settings come from the environment.
- **Quality failures** (a reply off its schema, a capped reply, a parse failing validation) don't count toward the breaker; they are counted per model separately, for diagnosis.
- With every model skipped, a call fails at once with "No model available" and the job ends `failed`.
- `model_disabled` and `model_restored` log events, the breaker state on `GET /health`, and an operational signal on any run during which a model was switched (OP-3).

## Alternatives rejected

- **Count bad output toward the breaker.** One hard document would switch off a healthy model for everyone.
- **Fallback output as low confidence.** Would send every fallback document to review even when its values check out.
- **Persisted metrics, dashboards, adaptive thresholds.** More than a single local process needs.

## Consequences

- A down model costs three failed calls, not one timeout per document.
- The breaker lives in one process: a restart forgets it, and a run resumed in another process starts with no switches recorded.

## What would change it

Several service processes (the breaker state would need to be shared), or a cloud provider with rate limits that need their own pacing.
