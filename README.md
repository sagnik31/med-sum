# Med-Sum

Med-Sum turns medical documents into readable summaries. A patient uploads prescriptions, lab reports or discharge summaries as PDFs or images. A vision-language model extracts the clinical content page by page, and a smaller text model writes a structured summary for each document. The goal is a cumulative patient summary that a doctor can read before a visit.

All model inference runs locally through [Ollama](https://ollama.com), so documents never leave the machine.

> **Status:** an early-stage personal project. Staff accounts, sessions, roles and staff management are in place, but patient records and an audit log are not yet (see [Limitations](#limitations)), so do not use it with real patient data. Generated summaries can be wrong and are not medical advice.

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

- **Frontend (React).** Login and password setup, document upload and listing, and a viewer for the generated summaries.
- **Backend (Go).** The public API. It handles accounts, login sessions and uploads, checks that a document belongs to the caller, and forwards processing requests to the AI service.
- **AI service (Python).** Runs the extraction and summarisation pipeline against local Ollama models.

When a document is uploaded, the pipeline runs as a background job. The document list and summary pages update themselves while it runs:

1. **Claim the job.** A row in `insights` with status `processing` acts as a lock, so the same document is never processed twice at once.
2. **Extract.** PDFs are rendered to images with PyMuPDF, and `qwen3.5:4b` transcribes each page into Markdown. The result is cached on the document, so retries skip this step.
3. **Summarise.** The same model turns the Markdown into an HTML summary, which is saved with status `completed`.
4. **Record failures.** Any error marks the job `failed` with a message, and the user can retry from the UI.

The cumulative patient summary is a separate background job, started from the Patient summary page. It combines the extracted Markdown of every processed document.

### Design notes

- **The AI service is not exposed to the browser.** Only the Go API calls it, after checking that the document belongs to the user, and every call carries a shared secret.
- **Recovery from crashed workers.** The worker updates a heartbeat before each page. A job with no heartbeat for 30 minutes can be taken over by a new request.
- **Untrusted model output is contained.** A document can carry text aimed at the model, so its output is treated as untrusted. The AI service strips scripts, styles, images, embeds, forms, event handlers and `javascript:` links from generated HTML. The UI shows it in a sandboxed iframe with scripts disabled and a content security policy that blocks every remote load, so the HTML cannot send data to another server.
- **Bounded work.** Uploads must be PDF, JPG or PNG files whose content matches the extension, are capped at 20 MB and 20 PDF pages, and are stored under random file names. Model output is capped per page and per summary so a looping model cannot run forever, and a model call that sends nothing for 10 minutes is abandoned.
- **Fallback model.** Both models read images and text. If `qwen3.5:4b` returns an error or hits the output cap, the same request goes to `gemma4:e2b`. If both fail, the job is marked `failed`, so cut-off output is never saved as a result.

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
| AI service | Python, FastAPI, PyMuPDF, Ollama Python client |
| Models | `qwen3.5:4b` (primary, extraction and summaries), `gemma4:e2b` (fallback) |
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
3. Open the document to read its summary once it shows **Ready**.
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
| `MEDSUM_APP_URL` | `medsumctl` | `http://localhost:5173`; where setup and reset links point |
| `MEDSUM_ENV`, `MEDSUM_DEV_PASSWORD` | `medsumctl seed-dev`, `dataset.load` | none; `seed-dev` runs only with `MEDSUM_ENV=dev` |
| `MEDSUM_PRIMARY_MODEL` | AI service | `qwen3.5:4b` |
| `MEDSUM_FALLBACK_MODEL` | AI service | `gemma4:e2b` |
| `MEDSUM_NUM_CTX` | AI service | `16384` (context window for each model call) |
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
| `GET` | `/documents/{id}/insight` | Get the summary and its status (`not_started`, `processing`, `completed`, `failed`) |
| `POST` | `/documents/{id}/insight` | Start or retry summary generation |
| `GET` | `/user/insights` | Get the cumulative patient summary and its status (`none`, `processing`, `completed`, `failed`) |
| `POST` | `/user/insights` | Start generating the cumulative patient summary |

Document and summary routes need the clinician role and `/admin/*` the admin role; without it the answer is 403. Accounts in another organisation are answered with 404.

The AI service exposes `GET /health` and two internal routes, `POST /internal/generate-insights` and `POST /internal/generate-user-insights`. Both require an `X-Internal-Token` header.

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
  src/components/        shared components (sandboxed HTML viewer)
  src/auth/              the logged-in account (AuthProvider), permissions and the route guard
  src/pages/             login, set password, account, staff, documents, document summary, patient summary
scripts/
  src/insights_service.py    FastAPI service and background job handling
  src/extract_report_slm.py  page extraction from images
  src/generate_insights_txt.py  summary generation
  src/llm.py                 model calls with the primary and fallback model
  src/record/                the structured record of a document and the schemas the model fills in
  src/derive/                values computed from the printed text: numbers, ranges, flags, unit table, dose schedules, dates
  src/validators/            deterministic checks of a record against its transcription and PDF text
  src/reset_db.py            resets the development database and uploads
  schemas/                   those schemas exported as JSON (regenerate: `python -m record.schemas` in src/)
  src/dataset/               synthetic dataset generator and loader
  evals/dataset/             the generated dataset: fictional documents, answer keys, transcriptions
  prompt/                prompts for extraction and summaries
  tests/                 pytest suite (fixtures in conftest.py)
  requirements.in        direct dependencies; requirements.txt pins every package
  requirements-dev.in    test tools; requirements-dev.txt pins them
postman/                 API collection
```

## Development

Install the Python test tools (pytest and [Ruff](https://docs.astral.sh/ruff/)) into the AI service's virtual environment:

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

The extraction and summary steps can also be run on their own, which is useful for tuning prompts. `python scripts/src/extract_report_slm.py` reads a sample report from `data/input/` and writes Markdown to `data/output/`; `python scripts/src/generate_insights_txt.py` turns that Markdown into HTML. The paths are set in `scripts/src/config.py`, and `data/` is not checked in.

### Synthetic dataset

[`scripts/evals/dataset/`](scripts/evals/dataset/README.md) holds 17 fictional documents (lab reports and prescriptions for 4 patients) with an answer key and expected transcription for each. Regenerate it with `python -m dataset.build` from `scripts/src`. `python -m dataset.load` shows what it would upload through the backend; `--yes` uploads it, which starts processing on the AI service (live model runs). It logs in as each patient's development account, so run `medsumctl seed-dev` first and set `MEDSUM_DEV_PASSWORD`.

## Limitations

- **Authentication is not finished.** Accounts, sessions, roles and staff management work, but each account still sees only the documents it uploaded (patient records shared within an organisation come later), and there is no audit log and no second factor. The first admin of an organisation is created from the command line. Medical registration numbers are not verified. There is no email, so setup and reset links are handed over by whoever creates them.
- **One extraction prompt.** Lab reports and prescriptions share one prompt; the document type is not detected yet.
- **Patient summary ordering.** Reports are ordered by upload date, not by the date printed on them, and the summary is only regenerated on request.
- **Summaries are model-written HTML.** Values are not yet stored as structured data or checked against the source document.
- **No container setup** yet, and no CI.

## Roadmap

The next phase focuses on doctors and hospital groups:

- Patient records with organisation-scoped access, an audit log and a second factor
- Patient-controlled sharing of records with doctors
- Structured clinical data (lab values, medications, diagnoses) instead of HTML only
- Timelines, trends and flags for abnormal values
- Choosing the extraction prompt by document type

## License

[MIT](LICENSE)
