# CLAUDE.md

Guidance for Claude Code, and for anyone else, working on this repository. Read [README.md](README.md) for what Med-Sum is. The design reasons are in [docs/adr/](docs/adr/README.md), and the topic pages are under [docs/](docs/).

## What the system is

There are three services and one PostgreSQL database:

- `frontend/`: a React SPA (Vite, :5173). It talks only to the Go API, with a session cookie.
- `backend/`: the Go API (`net/http`, :8080). It handles accounts, sessions, uploads, permissions and the audit log, and forwards processing to the AI service with `X-Internal-Token`. The browser never calls the AI service.
- `scripts/`: the Python AI service (FastAPI, :9000, bound to 127.0.0.1). It runs the document pipeline (a LangGraph graph), the patient summary and the MCP server against two local Ollama models: `qwen3.5:4b` (primary) and `gemma4:e2b` (fallback).
- `db/schema.sql`: the whole schema, written by hand, with no migrations. To change it, edit the file and run `python -m reset_db --yes` from `scripts/src`. The least-privilege roles are in `db/ai_service_role.sql` and `db/api_role.sql`.

Where to look in `scripts/src/`:

| Area | Path |
|---|---|
| Graph, nodes, routing, low-confidence signals, replay | `pipeline/` |
| Every model call: fallback, circuit breaker, token caps | `llm.py`, `model_health.py` |
| Record schema: model output vs stored record | `record/` (`printed.py`, `document.py`) |
| Values code computes (numbers, ranges, flags, units, dates) | `derive/` |
| Deterministic checks, returning findings | `validators/` |
| Pages built by code; patient summary and its overview check | `views/`, `summary/` |
| Job queue, leases, heartbeat | `document_jobs.py` |
| Read-only tools and the MCP server | `tools/`, `medsum_mcp/` |
| Runs, steps, provenance, JSON logs | `observability/` |
| Synthetic dataset, evals, demo | `dataset/`, `evals/`, `demo/` |

The prompts are in `scripts/prompt/` and the exported JSON schemas in `scripts/schemas/`. A test keeps the exported schemas in step with `record/`. The full layout is in [docs/development.md](docs/development.md).

## Commands

Run these before every commit, for each part you touched. CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the same checks.

```bash
(cd backend  && go build ./... && go vet ./... && go test ./...)
(cd scripts  && ruff check src tests && ruff format --check src tests && pytest)
(cd scripts/src && python -m evals.faultcheck)   # the validators against injected faults; must not drop below its baseline
(cd frontend && npm run lint && npm test && npm run build)
```

- The Python commands run inside `scripts/.venv` (`pip install -r requirements.txt -r requirements-dev.txt`). Ruff's rules and line length (88) are pinned in `scripts/pyproject.toml`.
- Tests marked `db` (Python) and the Go `db` package create and drop throwaway databases (`med_sum_test_py`, `med_sum_test_go`) from `db/schema.sql`. They skip when Postgres is unreachable; `MEDSUM_REQUIRE_DB=1` makes that a failure. `pytest -m "not db"` runs the rest.
- Go code must pass `gofmt`. On Windows with `autocrlf=true`, check a file with `tr -d '\r' < f.go | gofmt -d`.
- Python dependencies: edit `scripts/requirements.in`, then `uv pip compile requirements.in --universal --python-version 3.10 -o requirements.txt`.
- Setup and every environment variable are in [docs/setup.md](docs/setup.md).

## Rules the code depends on

Don't break these without a new or amended ADR.

- **Models transcribe and parse; code decides.** Derived values, flags, dates, routing to review and every page are code, never model output ([0001](docs/adr/0001-extract-and-display.md), [0003](docs/adr/0003-printed-and-derived-values.md), [0004](docs/adr/0004-deterministic-checks-decide-routing.md), [0007](docs/adr/0007-pages-built-by-code.md)).
- **Never relax a check to make a document pass.** If a validator or signal looks wrong, report it with the evidence. Its catch rate is gated by `evals.faultcheck` and `scripts/evals/faultcheck_baseline.json`.
- **Every value cites its page and lines.** Nothing reaches a page or a tool answer without its citation ([0012](docs/adr/0012-shared-tool-layer.md)).
- **All model calls go through `llm.py`.** Only the two local models are used, and a cloud model only ever sees synthetic text ([0005](docs/adr/0005-local-models-with-fallback.md)).
- **Patient data stays out of logs, findings and audit details.** These hold ids, counts and field names, never values or names. Organisation scope is enforced in the SQL of every query, not in handlers, and every read of patient data writes an audit row ([0008](docs/adr/0008-patient-data-protection.md), [0010](docs/adr/0010-staff-access-and-security.md)).
- **Runs are durable and traceable.** Every step, prompt hash, model and reply is stored, and every write checks that the worker still holds its job's lease ([0006](docs/adr/0006-durable-traceable-runs.md)).
- **The three services stay consistent in every commit.** A status, field or route added in one lands with its counterpart in the others.

## How to work here

- **Tests come with the change.** Pytest goes in `scripts/tests/`, and Go tests sit next to the code. Frontend tests cover the plain modules (`src/record/`, `src/api/request.js`, `src/auth/links.js`) with Node's test runner.
- **Tests never call a model.** An autouse fake model client in `scripts/tests/conftest.py` fails a test on any call it wasn't told to expect. Go handlers are tested against an in-memory store fake (`backend/internal/httpapi/helpers_test.go`).
- **Live model runs are slow on a CPU and are not part of the checks.** Use `python -m evals.run --replay` (recorded replies) where cassettes exist, and ask before starting a live run (`--live`).
- **Synthetic data only.** Use the dataset in `scripts/evals/dataset/` (`python -m dataset.load --yes` uploads it). Never commit or open real patient documents; `data/` is gitignored for that reason.
- **Decisions go in ADRs, not in code comments or plans.** A new architectural decision gets `docs/adr/00NN-<kebab-name>.md` and a row in the [ADR index](docs/adr/README.md), using the sections the index lists. Code that follows a decision says `See docs/adr/00NN-<name>.md` and also states the rule in a sentence or two. Committed files never cite numbering from private planning notes; `scripts/tests/test_no_plan_references.py` enforces this.
- **Docs:** the README stays an overview. Detail goes in the matching `docs/` page, so update that page when behaviour, a setting or a route changes.
- **Commits:** one per working change, with a conventional message (`feat(scripts): ...`, `fix(backend): ...`, `docs(readme): ...`). The body says why, and what was and wasn't verified.
