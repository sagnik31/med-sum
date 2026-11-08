# Med-Sum

Med-Sum turns medical documents into readable summaries. A patient uploads prescriptions, lab reports or discharge summaries as PDFs or images. A vision-language model extracts the clinical content page by page, and a smaller text model writes a structured summary for each document. The goal is a cumulative patient summary that a doctor can read before a visit.

All model inference runs locally through [Ollama](https://ollama.com), so documents never leave the machine.

> **Status:** an early-stage personal project. Authentication is not implemented yet (see [Limitations](#limitations)), so do not use it with real patient data. Generated summaries can be wrong and are not medical advice.

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
    UI["React SPA<br/>(Vite, :5173)"] -->|"REST, JWT"| API["Go API<br/>(:8080)"]
    API -->|"internal token"| AI["Python AI service<br/>(FastAPI, :9000)"]
    API --> DB[("PostgreSQL")]
    AI --> DB
    AI --> OL["Ollama<br/>qwen3.5:4b (primary)<br/>gemma4:e2b (fallback)"]
```

The system has three services that share one PostgreSQL database:

- **Frontend (React).** Login, document upload and listing, and a viewer for the generated summaries.
- **Backend (Go).** The public API. It handles login and uploads, checks that a document belongs to the caller, and forwards processing requests to the AI service.
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

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18, React Router, Vite, Tailwind CSS |
| Backend | Go (`net/http`), pgx, JWT |
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
export JWT_SECRET="dev-secret"
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

### 6. Try it out

1. Open http://localhost:5173 and log in with any phone number and any OTP (the OTP is not checked yet).
2. Upload a lab report or prescription as a PDF, JPG or PNG. Processing starts straight away; the document list shows its status and updates itself. On a CPU each page takes a few minutes.
3. Open the document to read its summary once it shows **Ready**.
4. Open **Patient summary** and choose **Generate summary** to combine every processed document.

## Configuration

| Variable | Used by | Default |
|---|---|---|
| `DATABASE_URL` | Backend (required), AI service | AI service falls back to `postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable` |
| `JWT_SECRET` | Backend (required) | none |
| `INTERNAL_API_TOKEN` | Backend (required), AI service | none; must be the same in both services |
| `HTTP_ADDRESS` | Backend | `:8080` |
| `UPLOADS_DIR` | Backend | `./uploads` (resolved to an absolute path) |
| `INSIGHTS_SERVICE_URL` | Backend | `http://localhost:9000` |
| `MEDSUM_PRIMARY_MODEL` | AI service | `qwen3.5:4b` |
| `MEDSUM_FALLBACK_MODEL` | AI service | `gemma4:e2b` |
| `MEDSUM_NUM_CTX` | AI service | `16384` (context window for each model call) |
| `VITE_API_BASE_URL` | Frontend | `http://localhost:8080`, set in `frontend/.env.local` |

The backend allows CORS requests from `http://localhost:5173` only.

## API

All backend routes except `/healthz` and `/login` require an `Authorization: Bearer <token>` header.

| Method | Route | Description |
|---|---|---|
| `GET` | `/healthz` | Health check |
| `POST` | `/login` | Log in with `{phone_number, full_name?}`; returns a JWT and the user |
| `GET` | `/documents` | List the user's documents, newest first, each with its `insight_status` |
| `POST` | `/documents` | Upload a PDF, JPG or PNG (multipart field `file`, max 20 MB) and start processing it |
| `DELETE` | `/documents/{id}` | Delete a document and its file |
| `GET` | `/documents/{id}/insight` | Get the summary and its status (`not_started`, `processing`, `completed`, `failed`) |
| `POST` | `/documents/{id}/insight` | Start or retry summary generation |
| `GET` | `/user/insights` | Get the cumulative patient summary and its status (`none`, `processing`, `completed`, `failed`) |
| `POST` | `/user/insights` | Start generating the cumulative patient summary |

The AI service exposes `GET /health` and two internal routes, `POST /internal/generate-insights` and `POST /internal/generate-user-insights`. Both require an `X-Internal-Token` header.

A Postman collection is in [`postman/collections/`](postman/collections/med-sum.postman_collection.json). It uses the variables `backend_url`, `ai_service_url`, `auth_token` (from the login response) and `internal_api_token`.

## Project structure

```
backend/
  cmd/server/            entry point: configuration, CORS, HTTP server
  internal/httpapi/      handlers, JWT auth, forwarding to the AI service
  internal/db/           PostgreSQL data access (pgx)
db/schema.sql            the whole database schema (no migrations; see step 2 of setup)
frontend/
  src/api/               API client
  src/components/        shared components (sandboxed HTML viewer)
  src/pages/             login, documents, document summary, patient summary
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

[`scripts/evals/dataset/`](scripts/evals/dataset/README.md) holds 17 fictional documents (lab reports and prescriptions for 4 patients) with an answer key and expected transcription for each. Regenerate it with `python -m dataset.build` from `scripts/src`. `python -m dataset.load` shows what it would upload through the backend; `--yes` uploads it, which starts processing on the AI service (live model runs).

## Limitations

- **No real authentication.** Any phone number receives a token, and the OTP field is not verified.
- **One extraction prompt.** Lab reports and prescriptions share one prompt; the document type is not detected yet.
- **Patient summary ordering.** Reports are ordered by upload date, not by the date printed on them, and the summary is only regenerated on request.
- **Summaries are model-written HTML.** Values are not yet stored as structured data or checked against the source document.
- **No container setup** yet, and no CI.

## Roadmap

The next phase focuses on doctors and hospital groups:

- Real OTP authentication and role-based access for patients, doctors and organisations
- Patient-controlled sharing of records with doctors
- Structured clinical data (lab values, medications, diagnoses) instead of HTML only
- Timelines, trends and flags for abnormal values
- Choosing the extraction prompt by document type

## License

[MIT](LICENSE)
