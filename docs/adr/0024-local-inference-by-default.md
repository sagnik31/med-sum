# 0024. Local inference by default; a cloud model only for synthetic data

- **Status:** Accepted, 2026-11-07; the cloud option 2026-11-09, deferred 2026-11-13.

## Context

Patient documents shouldn't leave the hospital's machines, and the project runs on no paid infrastructure. Local 4B-parameter models on a laptop CPU take minutes per page, which makes iterating on prompts and running evals slow.

## Decision

- **All inference runs locally** through Ollama, with exactly two models: `qwen3.5:4b` as primary (vision and text) and `gemma4:e2b` as fallback. Which is primary is to be decided by the live evals.
- Calls are deterministic as far as Ollama allows: temperature 0, thinking off, a fixed context size, an output cap per call, Ollama's `stop` option for the end marker.
- **A cloud provider is allowed only as an option, and only for synthetic data.** The candidate is a free tier (a cloud model's free tier may use prompts for training). Page images print the patient's name, so text redaction can't protect them: a cloud provider gets documents marked synthetic only, and that rule belongs in `llm.py`, where every call passes, rather than with each caller. No paid tier.
- Whether the cloud option is built at all is decided once everything else is in.

## Alternatives rejected

- **A cloud model as the main path.** Patient data would leave the machine, and it costs money.
- **Larger local models.** Too slow on the target CPU, or too large for its memory.

## Consequences

- Everything runs on one machine with no account or key; a full dataset run takes hours.
- Results show what small local models can do with deterministic checks around them, which is the point of the project.

## What would change it

Faster local hardware, or the live evals showing neither local model qualifies on the safety gate.
