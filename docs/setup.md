# Setup and configuration

How to run Med-Sum on your own machine, what each setting does, and the database roles to use outside development.

## Getting started

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| [Go](https://go.dev/dl/) | 1.25+ | |
| [Node.js](https://nodejs.org/) | 22+ | includes npm |
| [Python](https://www.python.org/downloads/) | 3.10+ | |
| [PostgreSQL](https://www.postgresql.org/download/) | 17+ | the examples assume user `postgres`, password `postgres` |
| [Ollama](https://ollama.com/download) | 0.30.9+ | the two models need about 8 GB of disk; they run on a CPU, but a GPU is much faster |

### 1. Clone the repository

```bash
git clone https://github.com/sagnik31/med-sum.git
cd med-sum
```

### 2. Create the database

```bash
createdb -U postgres med_sum
psql -U postgres -d med_sum -f db/schema.sql
```

The whole schema is in `db/schema.sql`; there are no migrations, because the database holds synthetic data only. After the schema changes, reset the database from `scripts/src` (with the virtual environment of step 4 active). Without `--yes` it only lists what it would delete. With `--yes` it drops every table, loads `db/schema.sql` and deletes the files in `backend/uploads/`. It only works on a database on this machine, and needs a role that owns the tables (`--database-url`, else `DATABASE_URL`). When the AI service's role exists (see [Database roles](#database-roles)), the reset grants it its privileges again:

```bash
python -m reset_db --yes
```

If your PostgreSQL credentials differ, adjust `DATABASE_URL` in the next steps (see [Configuration](#configuration)).

### 3. Pull the models

```bash
ollama pull qwen3.5:4b
ollama pull gemma4:e2b
```

Make sure Ollama is running before you start the AI service.

### 4. Install dependencies

```bash
# Backend
cd backend
go mod download
cd ..

# Frontend
cd frontend
npm install
cd ..

# AI service (pinned versions, in a virtual environment)
cd scripts
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..
```

### 5. Start the services

Each service runs in its own terminal.

Secrets aren't in the repository. Copy `backend/.env.example` to `backend/.env.local` and `scripts/.env.example` to `scripts/.env.local`, and put the same `INTERNAL_API_TOKEN` in both (at least 32 characters; `python -c "import secrets; print(secrets.token_hex(32))"` makes one). In `backend/.env.local`, set `DATABASE_URL` to the `medsum_api` role once it exists (see [Database roles](#database-roles)); until then, the `postgres` URL from step 2 works. Both `.env.local` files are gitignored.

**Windows (PowerShell).** Each directory has a `run.ps1` that reads its `.env.local` and starts the service:

```powershell
cd backend;  .\run.ps1     # Go API on http://localhost:8080
cd scripts;  .\run.ps1     # AI service on http://localhost:9000
cd frontend; .\run.ps1     # Web app on http://localhost:5173
```

**macOS / Linux:**

```bash
# Terminal 1: Go API on http://localhost:8080
cd backend
set -a; . ./.env.local; set +a   # DATABASE_URL, INTERNAL_API_TOKEN, ...
go run ./cmd/server
```

```bash
# Terminal 2: AI service on http://localhost:9000
cd scripts
source .venv/bin/activate
set -a; . ./.env.local; set +a   # INTERNAL_API_TOKEN (the backend's), MEDSUM_ENV=dev
uvicorn insights_service:app --app-dir src --reload --host 127.0.0.1 --port 9000
```

```bash
# Terminal 3: web app on http://localhost:5173
cd frontend
npm run dev
```

### 6. Create the development accounts

There is no sign-up and there are no default passwords. For local development, `medsumctl seed-dev` creates an organisation ("Dev Hospital"), an admin (`dev-admin@med-sum.test`), and a clinician (`dev-clinician@med-sum.test`), both with the password you choose (at least 12 characters):

```bash
cd backend
export DATABASE_URL="postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
export MEDSUM_ENV=dev
export MEDSUM_DEV_PASSWORD="choose a long dev password"
go run ./cmd/medsumctl seed-dev
```

On PowerShell, set the same variables with `$env:NAME = "value"`.

The API stops gracefully on Ctrl+C or SIGTERM: it takes no new requests and gives those in flight up to two minutes to finish. If it was killed between writing an upload's file and inserting its row, `go run ./cmd/medsumctl orphan-uploads` (with `DATABASE_URL`, and `UPLOADS_DIR` or `-dir`) lists the files in the uploads directory that no document names. It deletes nothing; check each and remove it by hand.

### 7. Try it out

1. Open http://localhost:5173 and log in as `dev-clinician@med-sum.test` with your `MEDSUM_DEV_PASSWORD`.
2. On **Patients**, choose **New patient** and give at least an MRN and a name (synthetic ones: see [Synthetic dataset](development.md#synthetic-dataset)), or open an existing patient.
3. Upload a lab report or prescription as a PDF, JPG or PNG to the patient. Processing starts straight away; the document list shows its status and updates itself. On a CPU each page takes a few minutes.
4. Open the document once it shows **Checks passed, awaiting sign-off** or **Needs review** to read the values extracted from it; a document that needs review says which values to check and why, and one whose checks passed waits for a clinician to read it against the upload and sign it off. **Processing details** and **Full trace** below show how it was produced.
5. Open the patient's **Patient summary** and choose **Generate summary** to combine every processed document.

## Configuration

| Variable | Used by | Default |
|---|---|---|
| `DATABASE_URL` | Backend (required), AI service (required unless `MEDSUM_ENV=dev`) | none. With `MEDSUM_ENV=dev` the AI service falls back to the local `med_sum` database as `postgres`; otherwise it refuses to start. Point the AI service at the `medsum_ai` role (see [Database roles](#database-roles)) |
| `INTERNAL_API_TOKEN` | Backend and AI service (both required) | none; the same in both services, at least 32 characters, or neither starts |
| `HTTP_ADDRESS` | Backend | `:8080` (`backend/.env.example` uses `127.0.0.1:8080`) |
| `UPLOADS_DIR` | Backend | `./uploads` (resolved to an absolute path) |
| `INSIGHTS_SERVICE_URL` | Backend | `http://localhost:9000` |
| `REVIEW_APPLY_TIMEOUT` | Backend | `60s`; how long saving a clinician's review waits for the AI service (a Go duration, at most `10m`; the server's write timeout, 2 minutes, is lengthened when this needs it). The queue calls keep 10 s |
| `ALLOWED_ORIGINS` | Backend | `http://localhost:5173`; comma-separated browser origins that may call the API |
| `TRUSTED_PROXIES` | Backend | empty (`backend/.env.example` sets `127.0.0.1,::1`, the Vite proxy); comma-separated IP addresses of the proxies in front of the API. A request from one counts as coming from the last address in `X-Forwarded-For` that isn't a trusted proxy, for the login limit, sessions and the audit log; the header is ignored from anyone else. Without it, behind a proxy every user shares one login limit |
| `COOKIE_SECURE` | Backend | `true`; set `false` only for a browser that won't keep a `Secure` cookie on plain-HTTP `localhost` |
| `MEDSUM_APP_URL` | Backend, `medsumctl` | `http://localhost:5173`; where setup and reset links point |
| `MEDSUM_ENV`, `MEDSUM_DEV_PASSWORD` | `medsumctl seed-dev`, `dataset.load`, AI service | none; `seed-dev` runs only with `MEDSUM_ENV=dev`, and only then may the AI service and the eval runner fall back to the local database |
| `MEDSUM_EVAL_ADMIN_URL` | `evals.run` (required unless `MEDSUM_ENV=dev`) | none; a connection to the server where the eval runner creates and drops its throwaway database. With `MEDSUM_ENV=dev`, the local server as `postgres` |
| `OLLAMA_HOST` | AI service, evals | `127.0.0.1:11434`; Ollama's own setting. It must be this machine (localhost, a loopback address or `0.0.0.0`): the AI service refuses to start otherwise, and every model is treated as a cloud model (ADR 0005) |
| `MEDSUM_PRIMARY_MODEL` | AI service | `qwen3.5:4b` |
| `MEDSUM_CLOUD_MODELS` | AI service | none; comma-separated model names to treat as cloud models (case ignored). Only the two configured models are ever local |
| `MEDSUM_FALLBACK_MODEL` | AI service | `gemma4:e2b` |
| `MEDSUM_NUM_CTX` | AI service | `16384` (context window for each model call) |
| `MEDSUM_WORKER` | AI service | `1`; `0` starts the service without its document worker (nothing queued is processed) |
| `MEDSUM_REPAIR`, `MEDSUM_REPAIR_TOOL_TURNS` | AI service | `fixed`, `3`; `tools` lets a repair read the document with read-only tools first, within that many model turns per repair (the fix's included) |
| `MEDSUM_BREAKER_FAILURES`, `MEDSUM_BREAKER_COOLDOWN_S` | AI service | `3`, `900`; consecutive model errors or timeouts that disable a model, and how long it stays disabled before it is tried again |
| `MEDSUM_AUDIT_SAMPLE_RATE` | AI service | `0.10`; the share of `checks_passed` documents sent to a clinician as an audit, every value checked before the sign-off (0 to 1) |
| `MEDSUM_JOB_LEASE`, `MEDSUM_JOB_HEARTBEAT_S`, `MEDSUM_JOB_MAX_ATTEMPTS` | AI service | `5 minutes`, `60`, `3`; how long a claim lasts, how often it is renewed, and how many claims a job gets before it is set aside |
| `MEDSUM_LOG_FILE` | AI service logs (`observability`) | `scripts/logs/medsum.jsonl`; JSON log lines (ids, events, timings) without patient content, also on stderr. Set up when the service starts |
| `VITE_API_BASE_URL` | Frontend | `/api`, set in the committed `frontend/.env` (it holds no secret; a `frontend/.env.local` overrides it and stays untracked); the Vite dev server proxies it to the backend |
| `MEDSUM_API_TARGET` | Frontend (Vite proxy) | `http://localhost:8080` |

The AI service reads its database settings and its token in one place, `scripts/src/settings.py`, and checks them when it starts: outside `MEDSUM_ENV=dev`, a missing `DATABASE_URL` stops it at start-up instead of connecting somewhere by default, and a missing or short `INTERNAL_API_TOKEN` stops it everywhere. The Go server checks its own in `cmd/server/config.go`.

## Database roles

The development setup connects as `postgres`, a superuser. Anywhere else, give the AI service its own role, `medsum_ai`, with only the privileges it uses ([`db/ai_service_role.sql`](../db/ai_service_role.sql)). It can read documents and write their transcription, read and write processing state (insights, jobs, records, pipeline runs and steps, model calls) and its checkpoints, and read the patient details the checks and the summary need, plus the uploader's name. It cannot read emails, password hashes, sessions or setup links, delete or truncate rows, or create, alter or drop tables in `public`. Create it once per database, as a superuser, after loading the schema, then give it a password:

```bash
psql -U postgres -d med_sum -f db/ai_service_role.sql
psql -U postgres -d med_sum -c "\password medsum_ai"
```

Then start the AI service with `DATABASE_URL="postgres://medsum_ai:<password>@localhost:5432/med_sum?sslmode=disable"`. The script is safe to run again, and `python -m reset_db` runs it after a reset when the role exists, since dropping the tables drops their grants. `scripts/tests/test_ai_service_role.py` runs a document job and a patient summary as the role and checks what it is refused.

The Go API has its own role, `medsum_api` ([`db/api_role.sql`](../db/api_role.sql)), created and given a password the same way:

```bash
psql -U postgres -d med_sum -f db/api_role.sql
psql -U postgres -d med_sum -c "\password medsum_api"
```

It reads and writes accounts, sessions, links, patients and documents, adds and reads audit rows, and reads the AI service's results. It can't delete a patient, a document or an account (deleting a document in the app is an update), change or delete an audit row, change an organisation, read the jobs, prompts or pipeline tables, or create, alter or drop a table. `reset_db` grants it again after a reset when it exists, and `backend/internal/db/role_test.go` runs every query the API makes as the role and checks what it is refused. The admin tools (`reset_db`, `medsumctl`) connect as the schema's owner.
