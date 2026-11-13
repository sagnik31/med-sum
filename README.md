# Med-Sum

Med-Sum turns medical documents into checked, structured records that a doctor can read before a visit. Hospital staff upload lab reports and prescriptions as PDFs or images. A LangGraph pipeline has a local vision-language model transcribe each page and fill in a schema for the document's type; deterministic code then derives numbers, ranges, flags and dates, checks every value against the page it came from, and sends anything it can't confirm to review. Each document's page and the cumulative patient summary are built by code from those records, so every value shown is printed on a document and cites where.

All model inference runs locally through [Ollama](https://ollama.com), so documents never leave the machine.

> **Status:** an early-stage personal project. Staff accounts, sessions, roles and staff management are in place, but patient records and an audit log are not yet (see [Limitations](#limitations)), so do not use it with real patient data. The models can misread a document, so extracted values can be wrong; nothing here is medical advice. The repository holds synthetic data only.

## Contents

- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [API](#api)
- [Project structure](#project-structure)
- [Development](#development)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [License](#license)

## How it works

```mermaid
flowchart LR
    UI["React SPA<br/>(Vite, :5173)"] -->|"REST, session cookie"| API["Go API<br/>(:8080)"]
    API -->|"internal token"| AI["Python AI service<br/>(FastAPI, :9000)"]
    API --> DB[("PostgreSQL")]
    AI --> DB
    AI --> OL["Ollama<br/>qwen3.5:4b (primary)<br/>gemma4:e2b (fallback)"]
```

The system has three services that share one PostgreSQL database:

- **Frontend (React).** Login and password setup, staff management, document upload and listing with each document's status, and viewers for the document pages and the patient summary.
- **Backend (Go).** The public API. It handles accounts, login sessions and uploads, checks that a document belongs to the caller, serves each document's record with its citations, and forwards processing requests to the AI service.
- **AI service (Python).** Runs the document pipeline and the patient summary against local Ollama models, and builds both pages in code.

When a document is uploaded, the Go API asks the AI service to queue it. A worker in the AI service takes queued documents one at a time and runs each through the document pipeline, a [LangGraph](https://langchain-ai.github.io/langgraph/) graph. The document list and summary pages update themselves while it runs:

1. **Transcribe.** PDFs are rendered to images with PyMuPDF, and `qwen3.5:4b` transcribes each page into numbered lines, alongside the PDF's own text where it has one.
2. **Classify.** The model labels the document as a lab report, a prescription or something else, and a keyword check confirms the label. Anything else keeps its transcription and goes to review.
3. **Parse.** The model fills in the schema for that type from the transcription. A reply that doesn't fit the schema is sent back with what was wrong, up to twice; then the fallback model gets three tries of its own.
4. **Validate.** Code builds the record (numbers, ranges, flags, dates) and checks it against the transcription and the PDF text. Errors go back to the model for repair, like a reply that doesn't fit.
5. **Render.** Code builds the document's page from the record, with no model call: every value exactly as printed, values computed by code (the document date, a flag from the printed range, a unit conversion, doses a day) marked as derived, and nothing the record doesn't hold, so no "abnormal" or severity of its own. A value that awaits review shows why, in plain words.
6. **Finish.** The record is saved with what each value traces back to. A document ends `verified`, or `needs_review` when a low-confidence signal fired (a value the checks couldn't confirm, a cut-off page, a patient name that doesn't match the account, and others); the document page then says which values to check. Anything that can't be recovered from ends `failed` with a message, and the user can retry from the UI.

Each document's run, its steps, and the prompt and model used at each are stored in the database. **Reprocess** on the summary page runs the document again from the start, for when the pipeline has changed; the new run's record replaces the last. A page whose image, model and transcription prompt are the same as in the document's earlier run is not transcribed again: the earlier run's lines are reused, and the step records which step they came from.

The cumulative patient summary is a separate background job, started from the Patient summary page. Once a user has one, it is rebuilt after each of their documents finishes (once the last of a batch is done, so a batch rebuilds it once). It is built by code from the records of every document that has finished (`verified` or `needs_review`):

- **Order.** Documents are ordered by the date printed on them (the report date, else the sample collection date; a prescription's date), not by upload. A document with no date is listed last, marked undated, and never used for a trend or as "the latest".
- **Sections.** The patient as printed, with warnings (undated documents, values awaiting review, documents not included); values with a printed flag or outside their printed range in the latest report of each test, and recorded allergies; the medicines on the latest prescription, then earlier ones; diagnoses and impressions exactly as printed; lab results and vitals over time under their printed panels; remarks with what they are printed on; and every document in date order with its review state. Every value cites its document, page and lines.
- **Trends** are only "rising" or "falling": two or more values of a test in the same printed unit, on different dates, each higher (or lower) than the last, with "based on N reports" and the reports named. No "improving", "worsening" or confidence labels.
- **Values awaiting review** are included, marked "awaiting review" with the reason in plain words, and left out of trends.
- **The overview** is the only model-written part: a few sentences the model writes from the computed facts alone. Code checks it before it is shown. A number must be one of the facts and stand near what the facts say it is (TSH's value next to TSH, a report's date next to that report's test, a count next to "documents"), and a name with digits such as "T4" must appear whole. Every word must be a word of the facts or one of a fixed list of ordinary words, so a test, medicine or diagnosis that isn't in the facts can't get through. A reply that fails is sent back once with what failed. If the second reply fails too, or no model answers, the summary is still saved without the overview and says why.

### Design notes

- **The AI service is not exposed to the browser.** Only the Go API calls it, after checking that the document belongs to the user, and every call carries a shared secret.
- **Recovery from crashed workers.** A job is claimed with a lease of 5 minutes, which a heartbeat renews every minute while the document runs. When a worker dies, its lease runs out and another worker takes the job over, carrying on from the graph's last checkpoint (kept in Postgres) instead of starting again. A job claimed three times without finishing is set aside, and every write a worker makes checks that it still holds the claim. The patient summary keeps a simpler lock: a summary with no progress for 30 minutes can be taken over by a new request, and the run that lost it can no longer write its result or failure.
- **Untrusted model output is contained.** A document can carry text aimed at the model, so what the models read off it is treated as untrusted. No page is model-written HTML: the document page and the patient summary are built by code, and every printed value is escaped, so text that looks like HTML is shown as text. The one piece of model-written prose, the summary's overview, is checked word by word and escaped too. The UI shows each page in a sandboxed iframe with scripts disabled and a content security policy that blocks every remote load, so the HTML cannot send data to another server.
- **Bounded work.** Uploads must be PDF, JPG or PNG files whose content matches the extension, are capped at 20 MB and 20 PDF pages, and are stored under random file names. Model output is capped per page and per summary so a looping model cannot run forever, and a model call that sends nothing for 10 minutes is abandoned.
- **Fallback model and circuit breaker.** Both models read images and text. If `qwen3.5:4b` returns an error or hits the output cap, the same request goes to `gemma4:e2b`. If both fail, the job is marked `failed`, so cut-off output is never saved as a result. A model whose server errors or times out three times in a row is skipped for 15 minutes, so requests go straight to the other model; after that one call tries it again. A document whose run switched a model off or back on carries an operational signal saying so, stored with the run; it doesn't send the document to review. With both skipped, a job fails at once with "No model available". A reply the pipeline can't use (off its schema, or failing the checks) is counted per model for diagnosis but doesn't trip the breaker. `GET /health` shows each model's state.

### Accounts and login

The app is for hospital staff; patients have no accounts.

- **Accounts are created, never signed up for.** `medsumctl create-admin` and `create-user` create an account and print a one-time setup link (valid for 72 hours) that the person opens to choose their own password; `medsumctl reset-link` issues a reset link (24 hours). The token is in the link's URL fragment, which browsers don't send to servers, and only its SHA-256 is stored.
- **Passwords** are hashed with argon2id (OWASP parameters). They need at least 12 characters, and common ones are refused. Five wrong passwords in a row lock the account for 15 minutes. Every failed login gets the same answer, and an unknown email takes as long to refuse as a wrong password, so the login form can't be used to find out which accounts exist.
- **Sessions** are rows in the database, not tokens the browser can read. The browser holds a random value in an `HttpOnly`, `SameSite=Strict` cookie; the database holds only its hash. A session ends after 30 minutes without a request or 12 hours after login. Logging out, changing the password or disabling the account ends sessions at once.
- **Roles:** a *clinician* works with documents and summaries; an *admin* manages staff accounts and sees no clinical data. Someone who needs both holds both roles. The API checks a permission on every route (`backend/internal/authz`), and the frontend shows only the pages the account may use.
- **Staff management:** an admin invites staff from the Staff page, which shows a setup link to pass on, and can change roles, disable or enable an account, or issue a password reset link. Disabling an account or changing its roles logs it out everywhere. Admins can't disable themselves or drop their own admin role, and an organisation always keeps at least one active admin.
- **Your sessions:** the Account page lists where you are logged in and can log out any of those sessions, or all but the current one.
- **CSRF:** besides `SameSite=Strict`, every request that changes something must carry an `X-Requested-With` header, which another site can't add without passing a CORS check that only `ALLOWED_ORIGINS` pass.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18, React Router, Vite, Tailwind CSS |
| Backend | Go (`net/http`), pgx, argon2id passwords, server-side sessions |
| AI service | Python, FastAPI, PyMuPDF, psycopg 3, Ollama Python client |
| Pipeline | LangGraph, with Postgres checkpoints; Pydantic record schemas |
| Models | `qwen3.5:4b` (primary: transcription, classification, parsing, the summary's overview), `gemma4:e2b` (fallback) |
| Database | PostgreSQL |

## Getting started

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| [Go](https://go.dev/dl/) | 1.25+ | |
| [Node.js](https://nodejs.org/) | 18+ | includes npm |
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

The whole schema is in `db/schema.sql`; there are no migrations, because the database holds synthetic data only. After the schema changes, reset the database from `scripts/src` (with the virtual environment of step 4 active). Without `--yes` it only lists what it would delete. With `--yes` it drops every table, loads `db/schema.sql` and deletes the files in `backend/uploads/`. It only works on a database on this machine:

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

**Windows (PowerShell).** Each directory has a `run.ps1` that sets development values for the environment variables:

```powershell
cd backend;  .\run.ps1     # Go API on http://localhost:8080
cd scripts;  .\run.ps1     # AI service on http://localhost:9000
cd frontend; .\run.ps1     # Web app on http://localhost:5173
```

**macOS / Linux:**

```bash
# Terminal 1: Go API on http://localhost:8080
cd backend
export DATABASE_URL="postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
export INTERNAL_API_TOKEN="dev-internal-token"
go run ./cmd/server
```

```bash
# Terminal 2: AI service on http://localhost:9000
cd scripts
source .venv/bin/activate
export INTERNAL_API_TOKEN="dev-internal-token"   # must match the backend
uvicorn insights_service:app --app-dir src --reload --port 9000
```

```bash
# Terminal 3: web app on http://localhost:5173
cd frontend
npm run dev
```

### 6. Create the development accounts

There is no sign-up and there are no default passwords. For local development, `medsumctl seed-dev` creates an organisation ("Dev Hospital"), an admin (`dev-admin@med-sum.test`), a clinician (`dev-clinician@med-sum.test`) and one account per synthetic patient (see [Synthetic dataset](#synthetic-dataset)), all with the password you choose (at least 12 characters):

```bash
cd backend
export DATABASE_URL="postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
export MEDSUM_ENV=dev
export MEDSUM_DEV_PASSWORD="choose a long dev password"
go run ./cmd/medsumctl seed-dev
```

On PowerShell, set the same variables with `$env:NAME = "value"`.

### 7. Try it out

1. Open http://localhost:5173 and log in as `dev-clinician@med-sum.test` with your `MEDSUM_DEV_PASSWORD`.
2. Upload a lab report or prescription as a PDF, JPG or PNG. Processing starts straight away; the document list shows its status and updates itself. On a CPU each page takes a few minutes.
3. Open the document once it shows **Verified** or **Needs review** to read the values extracted from it; a document that needs review says which values to check and why.
4. Open **Patient summary** and choose **Generate summary** to combine every processed document.

## Configuration

| Variable | Used by | Default |
|---|---|---|
| `DATABASE_URL` | Backend (required), AI service | AI service falls back to `postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable` |
| `INTERNAL_API_TOKEN` | Backend (required), AI service | none; must be the same in both services |
| `HTTP_ADDRESS` | Backend | `:8080` |
| `UPLOADS_DIR` | Backend | `./uploads` (resolved to an absolute path) |
| `INSIGHTS_SERVICE_URL` | Backend | `http://localhost:9000` |
| `ALLOWED_ORIGINS` | Backend | `http://localhost:5173`; comma-separated browser origins that may call the API |
| `COOKIE_SECURE` | Backend | `true`; set `false` only for a browser that won't keep a `Secure` cookie on plain-HTTP `localhost` |
| `MEDSUM_APP_URL` | Backend, `medsumctl` | `http://localhost:5173`; where setup and reset links point |
| `MEDSUM_ENV`, `MEDSUM_DEV_PASSWORD` | `medsumctl seed-dev`, `dataset.load` | none; `seed-dev` runs only with `MEDSUM_ENV=dev` |
| `MEDSUM_PRIMARY_MODEL` | AI service | `qwen3.5:4b` |
| `MEDSUM_FALLBACK_MODEL` | AI service | `gemma4:e2b` |
| `MEDSUM_NUM_CTX` | AI service | `16384` (context window for each model call) |
| `MEDSUM_WORKER` | AI service | `1`; `0` starts the service without its document worker (nothing queued is processed) |
| `MEDSUM_BREAKER_FAILURES`, `MEDSUM_BREAKER_COOLDOWN_S` | AI service | `3`, `900`; consecutive model errors or timeouts that disable a model, and how long it stays disabled before it is tried again |
| `MEDSUM_JOB_LEASE`, `MEDSUM_JOB_HEARTBEAT_S`, `MEDSUM_JOB_MAX_ATTEMPTS` | AI service | `5 minutes`, `60`, `3`; how long a claim lasts, how often it is renewed, and how many claims a job gets before it is set aside |
| `MEDSUM_LOG_FILE` | AI service logs (`observability`) | `scripts/logs/medsum.jsonl`; JSON log lines (ids, events, timings) without patient content, also on stderr. Set up when the service starts |
| `VITE_API_BASE_URL` | Frontend | `/api`, set in `frontend/.env.local`; the Vite dev server proxies it to the backend |
| `MEDSUM_API_TARGET` | Frontend (Vite proxy) | `http://localhost:8080` |

## API

All backend routes except `/healthz`, `/auth/login`, `/auth/logout` and `/auth/setup*` need a session: the `medsum_session` cookie that `/auth/login` sets. Every request other than `GET` must also carry `X-Requested-With: med-sum`, and a browser request must come from an origin in `ALLOWED_ORIGINS` (CSRF protection).

| Method | Route | Description |
|---|---|---|
| `GET` | `/healthz` | Health check |
| `POST` | `/auth/login` | Log in with `{email, password}`; sets the session cookie and returns the account |
| `POST` | `/auth/logout` | End the session |
| `GET` | `/auth/me` | The logged-in account: id, email, name, roles, permissions, organisation |
| `POST` | `/auth/password` | Change the password with `{current_password, new_password}`; ends the account's other sessions |
| `POST` | `/auth/setup/info` | `{token}` from a setup or reset link: whose account it is for |
| `POST` | `/auth/setup` | Set a password with `{token, password}` from a setup or reset link |
| `GET` | `/auth/sessions` | Your live sessions, with the current one marked |
| `DELETE` | `/auth/sessions` | End all your sessions except the current one |
| `DELETE` | `/auth/sessions/{id}` | End one of your sessions |
| `GET` | `/admin/users` | Staff of your organisation (admins) |
| `POST` | `/admin/users` | Invite `{email, full_name, registration_no?, roles}`; returns the account and its setup link (admins) |
| `PATCH` | `/admin/users/{id}` | Change `{roles?, status?, full_name?, registration_no?}`; `status` is `active` or `disabled` (admins) |
| `POST` | `/admin/users/{id}/reset-link` | A new setup link (invited account) or reset link (active account) (admins) |
| `GET` | `/documents` | List the user's documents, newest first, each with its `insight_status` |
| `POST` | `/documents` | Upload a PDF, JPG or PNG (multipart field `file`, max 20 MB) and start processing it |
| `DELETE` | `/documents/{id}` | Delete a document and its file |
| `GET` | `/documents/{id}/insight` | Get the summary and its status (`not_started`, `processing`, `verified`, `needs_review`, `failed`) |
| `POST` | `/documents/{id}/insight` | Start or retry processing |
| `GET` | `/documents/{id}/record` | The document's structured record as stored: every printed and derived value, the page transcriptions, and each item's provenance. `citations` lists every value's page, lines and quoted text with its item's review status (`validated`, `low_confidence`, ...) and the reasons it was flagged. 404 before the document has a record |
| `POST` | `/documents/{id}/reprocess` | Process the document again from the start, as a new run |
| `GET` | `/user/insights` | Get the cumulative patient summary and its status (`none`, `processing`, `completed`, `failed`) |
| `POST` | `/user/insights` | Start generating the cumulative patient summary |

Document and summary routes need the clinician role and `/admin/*` the admin role; without it the answer is 403. Accounts in another organisation are answered with 404.

The AI service exposes `GET /health` (with each model's circuit-breaker state) and three internal routes: `POST /internal/generate-insights` and `POST /internal/reprocess-document` queue a document, and `POST /internal/generate-user-insights` starts the patient summary. All three require an `X-Internal-Token` header.

A Postman collection is in [`postman/collections/`](postman/collections/med-sum.postman_collection.json). It uses the variables `backend_url`, `ai_service_url`, `email`, `password` and `internal_api_token`; Postman keeps the session cookie from the login request.

## Project structure

```
backend/
  cmd/server/            entry point: configuration, HTTP server
  cmd/medsumctl/         command line: organisations, accounts, setup and reset links, dev accounts
  internal/auth/         password hashing and rules, random tokens
  internal/authz/        roles and the permissions they grant
  internal/httpapi/      handlers, sessions, CORS and CSRF checks, forwarding to the AI service
  internal/db/           PostgreSQL data access (pgx)
db/schema.sql            the whole database schema (no migrations; see step 2 of setup)
frontend/
  src/api/               API client
  src/components/        shared components (sandboxed HTML viewer, sessions list)
  src/auth/              the logged-in account (AuthProvider), permissions and the route guard
  src/pages/             login, set password, account, staff, documents, document summary, patient summary
scripts/
  src/insights_service.py    FastAPI service; starts the document worker
  src/document_jobs.py       the document job queue: claims, leases, the worker
  src/pipeline/              the document graph: nodes, routing, low-confidence signals, checkpoints
  src/views/                 HTML built by code from records: the document page and the patient summary
  src/summary/               the patient summary: facts from the records, the overview and its check
  src/extract_report_slm.py  page transcription from images
  src/model_health.py        the circuit breaker per model
  src/llm.py                 model calls with the primary and fallback model
  src/record/                the structured record of a document and the schemas the model fills in
  src/derive/                values computed from the printed text: numbers, ranges, flags, unit table, dose schedules, dates
  src/validators/            deterministic checks of a record against its transcription and PDF text
  src/observability/         pipeline runs and steps in the database, provenance checks, JSON logs
  src/reset_db.py            resets the development database and uploads
  schemas/                   those schemas exported as JSON (regenerate: `python -m record.schemas` in src/)
  src/dataset/               synthetic dataset generator and loader
  src/evals/                 eval runner, scoring against the answer keys, fault check, model choice, reports
  evals/dataset/             the generated dataset: fictional documents, answer keys, transcriptions
  evals/faultcheck_baseline.json   the fault check's committed detection counts (its gate)
  evals/history/             one small JSON per live eval run (counts only), compared between runs
  evals/reports/             the full report of every live eval run, under the same name as its history entry
  evals/cassettes/           recorded model replies, so an eval can be replayed without Ollama
  prompt/                    prompts: page transcription, classify, parse per type, repair, the summary's overview
  tests/                 pytest suite (fixtures in conftest.py)
  requirements.in        direct dependencies; requirements.txt pins every package
  requirements-dev.in    test tools; requirements-dev.txt pins them
postman/                 API collection
```

## Development

Install the Python test tools (pytest, [Ruff](https://docs.astral.sh/ruff/) and Beautiful Soup, which the tests use to read the generated pages) into the AI service's virtual environment:

```bash
cd scripts && pip install -r requirements.txt -r requirements-dev.txt
```

Checks and tests for each part:

```bash
(cd backend  && go build ./... && go vet ./... && go test ./...)
(cd scripts  && ruff check src tests && ruff format --check src tests && pytest)
(cd frontend && npm run lint && npm run build)
```

The tests never call a model: model calls are replaced with fakes, so Ollama need not be running. The database tests create a throwaway database (`med_sum_test_go` for Go, `med_sum_test_py` for Python) from `db/schema.sql`, and drop it afterwards; your `med_sum` database is not touched. The Go tests need `psql` on the `PATH`. Both connect as `postgres:postgres@localhost:5432` unless `MEDSUM_TEST_ADMIN_URL` says otherwise. When Postgres can't be reached they are skipped; set `MEDSUM_REQUIRE_DB=1` to make that a failure. `pytest -m "not db"` runs only the tests that need no database.

Page transcription can also be run on its own, which is useful for tuning the prompt: `python scripts/src/extract_report_slm.py [file]` transcribes a file (by default a synthetic lab report from `scripts/evals/dataset/`) and writes Markdown to `data/output/`. The paths are set in `scripts/src/config.py`, and `data/` is not checked in.

### Synthetic dataset

[`scripts/evals/dataset/`](scripts/evals/dataset/README.md) holds 24 fictional documents (lab reports and prescriptions for 6 patients) with an answer key and expected transcription for each. Regenerate it with `python -m dataset.build` from `scripts/src`. `python -m dataset.load` shows what it would upload through the backend; `--yes` uploads it, which starts processing on the AI service (live model runs). It logs in as each patient's development account, so run `medsumctl seed-dev` first and set `MEDSUM_DEV_PASSWORD`.

Each document in the manifest also says how it should end (`expected_end_state`, and the routing signals it is built to raise: an undated prescription, a report printed for another patient, an unsupported document type) and which split it belongs to. d02 and d09 are `dev`: every prompt check so far ran on them. d18-d24 are `held_out`: two further patients, printed in layouts the other documents don't use (results as lines rather than a table, patient details in a key-value table or several to a line, the range before the unit, flags as words or arrows, two columns, numbered medicine lines, the strength in its own column, a table continued on the next page, dates with the month in words). No prompt or check is changed because of them; they show whether the numbers hold on layouts the pipeline wasn't built on. The other 15 are `test`, and reports show the three apart. On their answer keys, the code already reads three held-out items differently from a clinician: "Glycated Haemoglobin (HbA1c)" and "TSH (Ultrasensitive)" aren't recognised as HbA1c and TSH, and "Allergy: None known" isn't read as no known allergies. The tests list these gaps rather than fixing them on the held-out set.

### Evals

All of these run from `scripts/src`.

- **`python -m evals.faultcheck`** measures the checks themselves, with no model. It takes each answer key, injects one fault at a time (a digit swapped, a decimal point moved, a unit swapped, a range bound taken for the value, a dropped row or medicine, an invented result or medicine, a remark moved to another test, a changed strength or frequency, a day and month swapped, the document uploaded to another patient, a value misread in the transcription itself), runs the validators and signals on the altered copy, and counts the faults that raise a routing signal. It runs in a few seconds. It fails when a clean document ends anywhere but where the manifest expects, or when a fault is caught less often than the committed baseline. It caught 487 of 679 faults, 72% on the held-out documents and 71% on the test ones. Every fault in a value, unit, strength, frequency or date was caught, and so were invented items, dropped medicines and wrong patients. What gets through is listed under Limitations.
- **`python -m evals.run --live`** runs every document through the real graph, with each model as the primary, in a throwaway database (`med_sum_evals`). It scores each record and transcription against the answer key and checks each end state against the manifest. The report gives field precision and recall, unsafe errors, transcription accuracy and routing. It also gives the **risk-coverage line**: the share of documents that ended `verified` (no clinician looks at them) and the unsafe errors inside that share. Every count carries a 95% Wilson interval, because 24 documents can't support a bare percentage: 0 unsafe errors in 144 items still allows up to 2.6%. A live run of the whole dataset takes hours on a CPU. It records every model reply as a cassette. Each live run is kept in the repository: its full report (`report.md` to read, `report.json` with every document's scores and each wrong, missed or invented value) goes to [`scripts/evals/reports/`](scripts/evals/reports/README.md), and an entry with its counts and what produced it (code commit, prompt hashes, model digests) to `scripts/evals/history/`, both under the same name. A replay, or a live run with `--no-history`, writes its report to the gitignored `scripts/evals/results/` instead.
- **`python -m evals.run --replay`** runs the same graph from the cassettes, without Ollama. A changed prompt, page, option or output schema is a miss, and the run stops with a message naming what changed and asking for a re-record.
- **`python -m evals.compare A B`** compares two history entries or reports. It fails when unsafe errors rise (overall or in verified documents), when routing or medicine recall falls, or when field F1 or value recall falls by more than a point. **`python -m evals.history`** prints every run as a table.
- **`python -m evals.selfcheck`** scores the answer keys against themselves to show the report's shape. Every score is perfect, and 21 of 24 documents end verified.

## Limitations

- **Authentication is not finished.** Accounts, sessions, roles and staff management work, but each account still sees only the documents it uploaded (patient records shared within an organisation come later), and there is no audit log and no second factor. The first admin of an organisation is created from the command line. Medical registration numbers are not verified. There is no email, so setup and reset links are handed over by whoever creates them.
- **Vision misreads are only caught where a PDF has a text layer.** A scan or photo has no independent text to compare the model's reading with, so a value read wrongly but consistently passes the checks. Even with a text layer, the comparison is per page, so a misread that happens to match another number on the page passes. The fault check caught 55 of 77 such misreads, and all 22 it missed were of these two kinds.
- **Some wrong values pass the checks.** A range bound taken for the value is on the right line, so it is caught only when it changes the flag (32 of 98 in the fault check). A lab row the parse leaves out is only an information note, so the document can still end verified (3 of 107 were caught, through a remark left without its test). Both are listed by the fault check, not hidden.
- **No medical codes or brand-to-generic mapping.** Medicines are kept as written (brands stay brands), and no LOINC, SNOMED or RxNorm codes are assigned: the local models aren't reliable enough to map them.
- **Lab reports and prescriptions only.** Any other document is kept, transcribed and marked for review, but not parsed.
- **Not yet evaluated on the models.** The eval runner, the fault check and the synthetic dataset exist, and the checks have been measured, but the pipeline has not yet been run live over the dataset on both models. Citations and clinician corrections are not yet on screen.
- **The overview check is strict and literal.** It compares words, not meanings. A number counts as in context when one of the words around it (three either side) is a word of what it belongs to, so two tests that share a unit in one list can still trade values, and an ordinary word missing from its fixed list leaves the overview out. Its layout is a first draft.
- **No container setup** yet, and no CI.

## Roadmap

The next phase focuses on doctors and hospital groups:

- Patient records with organisation-scoped access, an audit log and a second factor
- Citations shown on screen, and a review screen where a clinician confirms or corrects flagged values
- Live eval runs on both models, reported for the dev, test and held-out documents apart

## License

[MIT](LICENSE)
