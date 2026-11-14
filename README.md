# Med-Sum

**Checked, cited records of a patient's lab reports and prescriptions, for the clinicians of a hospital.**

Patients arrive with a folder of reports from different laboratories, in different layouts, often photographed on a phone. Before a consultation a doctor has minutes to find the latest haemoglobin, what was prescribed last time and what changed. Med-Sum reads those documents for them. Staff upload each document to the patient's record. A local vision-language model transcribes every page and fills in a schema for the document's type. Deterministic code then derives numbers, ranges, flags and dates, checks every value against the page it came from, and sends anything it can't confirm to a clinician's review queue. Each document's page and the patient's cumulative summary are built by code from those records, so every value shown is printed on a document and cites the page and lines where.

All model inference runs on the hospital's own machine through [Ollama](https://ollama.com), so documents never leave it.

> **Status:** a personal project, not a medical device. It has not been validated with clinicians or on real documents, and the models can misread a page, so a value can be wrong; nothing here is medical advice. Staff accounts, patient records shared within an organisation, clinician review and an audit log of every read are built, but the deployment hardening a hospital needs is not (see [Limitations](#limitations)). The repository holds synthetic data only; do not use it with real patient data.

## Contents

- [For hospitals](#for-hospitals)
- [Case study](#case-study)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [API](#api)
- [MCP server](#mcp-server)
- [Project structure](#project-structure)
- [Development](#development)
- [Design decisions](docs/adr/README.md)
- [Where patient data goes, and the threat model](docs/data-and-threats.md)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [License](#license)

## For hospitals

**What a clinician gets.** For each patient, one summary built from every processed document: flagged and out-of-range values from the latest reports, the medicines on the latest prescription, diagnoses as printed, results over time under their printed panels, and every document in date order. Each value links to the lines of the document it was read from, shown beside the original upload. Nothing is paraphrased: values appear exactly as printed, and anything code computed (a flag from the printed range, a unit conversion, doses a day, the document's date) is labelled *derived*.

**What it never does.** It doesn't diagnose, grade severity, suggest treatment, map brands to generics, or fill in what a document doesn't say. A trend is only "rising" or "falling", from two or more reports in the same unit, with the reports named. The only model-written prose, the summary's few-sentence overview, is checked word by word against the facts before it is shown.

**How wrong values are caught.** In three layers, the first and the third measured:

1. **Deterministic checks decide what a clinician must see.** A document goes to `needs_review`, with the reason in plain words, when a value doesn't trace to its line, doesn't match the PDF's own text, disagrees with its printed range or flag, or carries an impossible date, or when the document was printed for another patient. Without any model, these checks catch 962 of 988 injected faults (97.4%, 95% CI 96.2% to 98.2%); what gets through is listed under [Limitations](#limitations).
2. **A clinician confirms or corrects, beside the reading.** The review queue lists documents awaiting review, oldest first. On the review screen the clinician sees the original upload next to each value, confirms it or enters the right value, and the correction is kept next to what the model read, never over it, with who and when. A corrected value runs the range, flag and date checks again. The summary then says "corrected by Dr X on date".
3. **A share of verified documents is audited.** Ten per cent of the documents that passed every check (`MEDSUM_AUDIT_SAMPLE_RATE`) go back to the queue as an audit, and the audit can only be finished once every value is confirmed or corrected. A correction there is an error the checks let through, so the escape rate is measured in use, and each signal's precision is measured from the reviews it caused.

**Where the data lives.** Documents, records and the audit log stay in the hospital's PostgreSQL and on its disk, and the models run locally. Each organisation's patients are visible to its own clinicians only, enforced in the SQL of every query. Every read or change of patient data writes an audit row (who, what, which patient, the outcome), and a database trigger refuses to change or delete one. Admins manage staff and read the audit log but see no clinical data. See [where patient data goes](docs/data-and-threats.md).

**What a deployment would still need.** Encryption at rest, TLS, single sign-on or a second factor, verification of clinicians' registration numbers, integration with the hospital's systems (HL7 or FHIR), and above all a clinical validation on the hospital's own documents. None of these is built; see [Limitations](#limitations).

## Case study

A local, small-model document pipeline for clinical use, built so that every number it shows can be traced and every error it lets through can be counted.

### The approach

- **Extract and display, never infer.** The models read and transcribe; code derives and checks. No model output reaches a clinician without a citation to a page and lines, and no clinical judgement is made by a model ([ADR 0001](docs/adr/0001-extract-and-display.md), [ADR 0003](docs/adr/0003-printed-and-derived-values.md)).
- **Two model steps per document**: transcribe each page into numbered lines, then parse the lines into a typed schema in parts small enough for a 4B model ([ADR 0002](docs/adr/0002-two-model-steps.md)).
- **Deterministic routing.** Whether a document needs a clinician is decided by code from named signals, not by a model's confidence ([ADR 0004](docs/adr/0004-deterministic-checks-decide-routing.md)).
- **Local models with a fallback** and a circuit breaker; a cloud model only ever for synthetic text ([ADR 0005](docs/adr/0005-local-models-with-fallback.md)).
- **Durable, traceable runs**: every step, prompt, model and reply stored, resumable after a crash, replayable step by step ([ADR 0006](docs/adr/0006-durable-traceable-runs.md)).
- **One read-only tool layer**, used by the repair step in-process and served to MCP clients through the API, with a citation on every answer and an audit row per call ([ADR 0012](docs/adr/0012-shared-tool-layer.md)).

### The pipeline graph

```mermaid
flowchart LR
    S((start)) --> open --> T["transcribe<br/>(each page)"]
    T --> T
    T --> C[classify]
    C -->|"lab report,<br/>prescription"| P["parse<br/>(each part)"]
    C -->|other| V[validate]
    P --> P
    P --> V
    P -->|"reply refused<br/>or cut off"| R[repair]
    V -->|error findings| R
    R --> V
    P -->|"tries<br/>used up"| F[fallback model]
    R -->|"tries<br/>used up"| F
    V -->|error findings left| F
    F --> P
    V --> RE[render] --> FI[finish]
    FI --> E1([verified])
    FI --> E2([needs_review])
```

A run that can't recover (an unreadable file, no model answering) ends `failed` with a message and can be retried. Each node's state is checkpointed in Postgres, so a crashed worker's run is carried on by another.

### Results

The evals run on a committed synthetic dataset of 25 documents for 6 fictional patients (lab reports, prescriptions and one other document; PDFs with and without a text layer, and images), reported for its dev (2), test (16) and held-out (7) documents apart. Every count carries a 95% Wilson interval: 25 documents can't support a bare percentage.

> Cells marked *pending* are filled from the first live eval run, whose full report will be committed to [`scripts/evals/reports/`](scripts/evals/reports/README.md). None is estimated.

**The document pipeline, per model** (`python -m evals.run --live`)

| Measure | `qwen3.5:4b` | `gemma4:e2b` |
|---|---|---|
| Printed-field precision | *pending* | *pending* |
| Printed-field recall | *pending* | *pending* |
| Printed-field F1 | *pending* | *pending* |
| Unsafe errors (a wrong value, unit, flag, date, strength or frequency) | *pending* | *pending* |
| Documents ending `verified` | *pending* | *pending* |
| Unsafe errors inside `verified` documents | *pending* | *pending* |
| End state as the manifest expects | *pending* | *pending* |
| Transcription value recall | *pending* | *pending* |
| Transcription character error rate | *pending* | *pending* |
| Seconds per page (CPU) | *pending* | *pending* |

**Repair** (`python -m evals.repair --live`, 676 injected-fault cases)

| Measure | Re-parse with the problems listed | Tool repair |
|---|---|---|
| Cases repaired | *pending* | *pending* |
| Unsafe errors left after repair | *pending* | *pending* |
| Model calls per repair | *pending* | *pending* |
| Seconds per repair | *pending* | *pending* |
| Tool calls malformed or unknown | n/a | *pending* |

**The checks without a model** (`python -m evals.faultcheck`, measured)

| Measure | Result |
|---|---|
| Injected faults caught | 962/988 (97.4%, 95% CI 96.2%-98.2%) |
| Faults in a value, unit, strength, frequency or date caught | all |
| Dropped lab results caught | 161/167 |
| Value misreads in the transcription caught | 110/130 (all 20 missed were on pages without a text layer) |

**In use, from clinicians' reviews** (`python -m evals.review_signals`)

| Measure | Result |
|---|---|
| Precision of each low-confidence signal (values a clinician corrected, of those it raised) | *pending* |
| Escape rate (audited `verified` documents holding a value a clinician corrected) | *pending* |

**An MCP client reading the records** (`python -m evals.mcp_eval --live`)

| Measure | Result |
|---|---|
| Value questions answered correctly, cited and grounded | *pending* |
| Answers whose every citation is one the tools returned | *pending* |
| Answers whose every number appears in what the tools returned | *pending* |
| Questions with no answer in the records, declined | *pending* |

**Prompt injection.** Text in a document aimed at the model (embedded instructions, hidden text, a remote image) is contained by design: pages are built by code with every value escaped, shown in a sandboxed frame with scripts off and no remote loads, and the overview is checked word by word. These defences are tested. A measured injection eval, scoring whether the record stays faithful when a document carries such text, is not built yet:

| Measure | Result |
|---|---|
| Records unchanged by an embedded instruction | *pending* |
| Pages with no remote load or script | *pending* |

### Design trade-offs

- **Small local models over a large cloud one.** Documents stay on the hospital's machine, at the price of minutes per page on a CPU and a parse split into parts. Whether a 4B model is good enough is exactly what the results above measure.
- **Strict checks over a smooth experience.** A document whose values can't all be traced goes to review, even when every value is in fact right. A clinician's time is the cost of an error rate that can be counted.
- **Code-built pages over model-written ones.** No model writes a page, so no model can add a diagnosis, a severity or an injected link. The only prose, the overview, is constrained to the facts' own words.
- **Synthetic data in the repository.** Everything here can be shared and re-run; nothing has been shown on real documents yet.

### Demo

A recorded walk-through (upload, processing, the review queue, a correction and the summary that follows) is *pending*, as is `python -m demo`, which will run two dataset documents through the real graph from recorded model replies, without Ollama, in under a minute.

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

- **Frontend (React).** Login and password setup, staff management, patients, document upload and listing with each document's status, and viewers for the document pages and the patient summary. Every value on a document's page shows the page and lines it was read from; choosing it highlights those lines in the page's transcription beside it. Values computed by code (numbers, flags from ranges, unit conversions, dose schedules, the document date) are labelled *derived*, and a flagged value shows why. Each source reference in the patient summary (`D2 p1:6`) opens its document at those lines. A review queue lists the documents awaiting a clinician, and the review screen shows the original upload beside each value to confirm or correct; each document's "Processing details" show how its run went, step by step.
- **Backend (Go).** The public API. It handles accounts, login sessions and uploads, checks that a document belongs to the caller, serves each document's record with its citations, and forwards processing requests to the AI service.
- **AI service (Python).** Runs the document pipeline and the patient summary against local Ollama models, and builds both pages in code.

When a document is uploaded, the Go API asks the AI service to queue it. A worker in the AI service takes queued documents one at a time and runs each through the document pipeline, a [LangGraph](https://langchain-ai.github.io/langgraph/) graph. The document list and summary pages update themselves while it runs:

1. **Transcribe.** PDFs are rendered to images with PyMuPDF, and `qwen3.5:4b` transcribes each page into numbered lines, alongside the PDF's own text where it has one. A page too long for one call's 4,096-token output cap is read once more as two overlapping halves, top and bottom, and code joins them where they share an exact run of lines; if they don't, the page goes to review.
2. **Classify.** The model labels the document as a lab report, a prescription or something else, and a keyword check confirms the label. Anything else keeps its transcription and goes to review.
3. **Parse.** The model fills in the schema for that type from the transcription, one page per call. A page of more than 25 table rows is read in parts cut between rows, and a part that starts inside a table is given that table's heading and header row; the parts' replies are merged into one. (One call on a 26-result report took about 5,000 output tokens, so a page of 40-odd rows would reach the 8,192-token cap.) A reply that doesn't fit the schema is sent back with what was wrong, up to twice, and only the parts that were wrong are asked again; then the fallback model gets three tries of its own.
4. **Validate.** Code builds the record (numbers, ranges, flags, dates) and checks it against the transcription and the PDF text. Errors go back to the model for repair, like a reply that doesn't fit. By default a repair re-parses with the problems listed. With `MEDSUM_REPAIR=tools` the model may first read the document through read-only tools (numbered lines of a page, a search of the transcription, the PDF's own text, an item of the record with the lines it cites), within a budget of three model turns per repair, the one giving the fix included. A malformed tool call uses its turn, and the validators judge the fix exactly as before. Tools run only inside a repair, so a document that parses cleanly costs no more. Which repair works better is measured, not assumed (see Evals).
5. **Render.** Code builds the document's page from the record, with no model call: every value exactly as printed, values computed by code (the document date, a flag from the printed range, a unit conversion, doses a day) marked as derived, and nothing the record doesn't hold, so no "abnormal" or severity of its own. A value that awaits review shows why, in plain words.
6. **Finish.** The record is saved with what each value traces back to. A document ends `verified`, or `needs_review` when a low-confidence signal fired (a value the checks couldn't confirm, a cut-off page, a model input that may have been cut short to fit the context window, a patient name that doesn't match the account, and others); the document page then says which values to check. Anything that can't be recovered from ends `failed` with a message, and the user can retry from the UI.

Each document's run, its steps, and the prompt and model used at each are stored in the database, with the git commit, output schemas and database structure the run used and the text of every prompt. `python -m pipeline.replay <step_id>` runs one stored transcribe, classify or parse step again and shows how the reply differs. **Reprocess** on the summary page runs the document again from the start, for when the pipeline has changed; the new run's record replaces the last. A page whose image, model and transcription prompt are the same as in the document's earlier run is not transcribed again: the earlier run's lines are reused, and the step records which step they came from.

The cumulative patient summary is a separate background job, started from the Patient summary page. Once a user has one, it is rebuilt after each of their documents finishes (once the last of a batch is done, so a batch rebuilds it once). It is built by code from the records of every document that has finished (`verified` or `needs_review`):

- **Order.** Documents are ordered by the date printed on them (the report date, else the sample collection date; a prescription's date), not by upload. A document with no date is listed last, marked undated, and never used for a trend or as "the latest".
- **Sections.** The patient as printed, with warnings (undated documents, values awaiting review, documents not included); values with a printed flag or outside their printed range in the latest report of each test, and recorded allergies; the medicines on the latest prescription, then earlier ones; diagnoses and impressions exactly as printed; lab results and vitals over time under their printed panels; remarks with what they are printed on; and every document in date order with its review state. Every value cites its document, page and lines.
- **Trends** are only "rising" or "falling": two or more values of a test in the same printed unit, on different dates, each higher (or lower) than the last, with "based on N reports" and the reports named. No "improving", "worsening" or confidence labels.
- **Values awaiting review** are included, marked "awaiting review" with the reason in plain words, and left out of trends.
- **The overview** is the only model-written part: a few sentences the model writes from the computed facts alone. Code checks it before it is shown. A number must be one of the facts and stand near what the facts say it is (TSH's value next to TSH, a report's date next to that report's test, a count next to "documents"), and a name with digits such as "T4" must appear whole. Every word must be a word of the facts or one of a fixed list of ordinary words, so a test, medicine or diagnosis that isn't in the facts can't get through. A reply that fails is sent back once with what failed. If the second reply fails too, or no model answers, the summary is still saved without the overview and says why.

### Design notes

The reasons behind these and the project's other main design choices, with the alternatives that were rejected, are recorded as [architecture decision records](docs/adr/README.md).

- **The AI service is not exposed to the browser.** Only the Go API calls it, after checking that the document belongs to the user, and every call carries a shared secret.
- **Recovery from crashed workers.** A job is claimed with a lease of 5 minutes, which a heartbeat renews every minute while the document runs. When a worker dies, its lease runs out and another worker takes the job over, carrying on from the graph's last checkpoint (kept in Postgres) instead of starting again. A job claimed three times without finishing is set aside, and every write a worker makes checks that it still holds the claim. The patient summary keeps a simpler lock: a summary with no progress for 30 minutes can be taken over by a new request, and the run that lost it can no longer write its result or failure.
- **Untrusted model output is contained.** A document can carry text aimed at the model, so what the models read off it is treated as untrusted. No page is model-written HTML: the document page and the patient summary are built by code, and every printed value is escaped, so text that looks like HTML is shown as text. The one piece of model-written prose, the summary's overview, is checked word by word and escaped too. The UI shows each page in a sandboxed iframe with scripts disabled and a content security policy that blocks every remote load, so the HTML cannot send data to another server.
- **Bounded work.** Uploads must be PDF, JPG or PNG files whose content matches the extension, are capped at 20 MB and 20 PDF pages, and are stored under random file names. Model output is capped per page and per summary so a looping model cannot run forever, and a model call that sends nothing for 10 minutes is abandoned.
- **Fallback model and circuit breaker.** Both models read images and text. If `qwen3.5:4b` returns an error or hits the output cap, the same request goes to `gemma4:e2b`. If both fail, the job is marked `failed`, so cut-off output is never saved as a result. A model whose server errors or times out three times in a row is skipped for 15 minutes, so requests go straight to the other model; after that one call tries it again. A document whose run switched a model off or back on carries an operational signal saying so, stored with the run; it doesn't send the document to review. With both skipped, a job fails at once with "No model available". A reply the pipeline can't use (off its schema, or failing the checks) is counted per model for diagnosis but doesn't trip the breaker. `GET /health` shows each model's state.

### Accounts and login

The app is for hospital staff; patients have no accounts. A patient is a record that the clinicians of one organisation share.

- **Accounts are created, never signed up for.** `medsumctl create-admin` and `create-user` create an account and print a one-time setup link (valid for 72 hours) that the person opens to choose their own password; `medsumctl reset-link` issues a reset link (24 hours). The token is in the link's URL fragment, which browsers don't send to servers, and only its SHA-256 is stored.
- **Passwords** are hashed with argon2id (OWASP parameters). They need at least 12 characters, and common ones are refused. Five wrong passwords in a row lock the account for 15 minutes. Every failed login gets the same answer, and an unknown email takes as long to refuse as a wrong password, so the login form can't be used to find out which accounts exist.
- **Sessions** are rows in the database, not tokens the browser can read. The browser holds a random value in an `HttpOnly`, `SameSite=Strict` cookie; the database holds only its hash. A session ends after 30 minutes without a request or 12 hours after login. Logging out, changing the password or disabling the account ends sessions at once.
- **Patients:** a clinician finds a patient by name or MRN (the hospital's patient ID) or adds one, with a date of birth or, when that isn't known, an age, and uploads documents to that patient's record. The pipeline checks the name, ID and date of birth printed on each document against the record. Another organisation's patients and documents answer 404, and the organisation is checked in the SQL of every query, not in the handlers. Only the clinician who uploaded a document can delete it; deleting hides it everywhere and rebuilds the patient summary, but keeps its rows and file, so what was uploaded stays accountable.
- **Roles:** a *clinician* works with patients, documents and summaries; an *admin* manages staff accounts and sees no clinical data. Someone who needs both holds both roles. The API checks a permission on every route (`backend/internal/authz`), and the frontend shows only the pages the account may use.
- **Staff management:** an admin invites staff from the Staff page, which shows a setup link to pass on, and can change roles, disable or enable an account, or issue a password reset link. Disabling an account or changing its roles logs it out everywhere. Admins can't disable themselves or drop their own admin role, and an organisation always keeps at least one active admin.
- **Audit log:** every login (and failed login), lockout, logout, password and staff change, and every read or change of patient data writes one row to `audit_events`: who, what, which record and patient, the outcome, the address and browser. It holds ids, never clinical content (no names, values or file names; details are counts, field names and reasons). A change writes its row in its own transaction, so both commit or neither does; a read writes its row before the data is sent, and is refused (500) when it can't. A trigger refuses UPDATE and DELETE on the table. Admins read their organisation's log on the Audit log page, filtered by staff account, patient and dates.
- **Your sessions:** the Account page lists where you are logged in and can log out any of those sessions, or all but the current one.
- **CSRF:** besides `SameSite=Strict`, every request that changes something must carry an `X-Requested-With` header, which another site can't add without passing a CORS check that only `ALLOWED_ORIGINS` pass.
- **Login attempts per address:** each address gets 10 attempts a minute on the routes that check a password or a link (`POST /auth/login`, `/auth/password`, `/auth/setup`, `/auth/setup/info`); more answer 429 with `Retry-After`, and the refusal is audited. This slows guessing across accounts, which the per-account lockout doesn't. The count is kept in memory, per API process.
- **Security headers** on every API response: `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` (responses carry patient data), and HSTS when cookies are `Secure`. The Vite server sends the framing, sniffing and referrer headers for the app's own pages.
- **Idle warning:** every response to a logged-in request says how many seconds the session has left (`X-Session-Expires-In`); two minutes before the end the app asks whether to stay signed in.
- **Secrets:** each service reads its secrets from an untracked `.env.local` (copied from the committed `.env.example`), never from a committed script. Both services refuse to start without `INTERNAL_API_TOKEN` of at least 32 characters; the AI service listens on `127.0.0.1` only, as only the Go API calls it.

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

### 7. Try it out

1. Open http://localhost:5173 and log in as `dev-clinician@med-sum.test` with your `MEDSUM_DEV_PASSWORD`.
2. On **Patients**, choose **New patient** and give at least an MRN and a name (synthetic ones: see [Synthetic dataset](#synthetic-dataset)), or open an existing patient.
3. Upload a lab report or prescription as a PDF, JPG or PNG to the patient. Processing starts straight away; the document list shows its status and updates itself. On a CPU each page takes a few minutes.
4. Open the document once it shows **Verified** or **Needs review** to read the values extracted from it; a document that needs review says which values to check and why.
5. Open the patient's **Patient summary** and choose **Generate summary** to combine every processed document.

## Configuration

| Variable | Used by | Default |
|---|---|---|
| `DATABASE_URL` | Backend (required), AI service (required unless `MEDSUM_ENV=dev`) | none. With `MEDSUM_ENV=dev` the AI service falls back to the local `med_sum` database as `postgres`; otherwise it refuses to start. Point the AI service at the `medsum_ai` role (see [Database roles](#database-roles)) |
| `INTERNAL_API_TOKEN` | Backend and AI service (both required) | none; the same in both services, at least 32 characters, or neither starts |
| `HTTP_ADDRESS` | Backend | `:8080` (`backend/.env.example` uses `127.0.0.1:8080`) |
| `UPLOADS_DIR` | Backend | `./uploads` (resolved to an absolute path) |
| `INSIGHTS_SERVICE_URL` | Backend | `http://localhost:9000` |
| `ALLOWED_ORIGINS` | Backend | `http://localhost:5173`; comma-separated browser origins that may call the API |
| `COOKIE_SECURE` | Backend | `true`; set `false` only for a browser that won't keep a `Secure` cookie on plain-HTTP `localhost` |
| `MEDSUM_APP_URL` | Backend, `medsumctl` | `http://localhost:5173`; where setup and reset links point |
| `MEDSUM_ENV`, `MEDSUM_DEV_PASSWORD` | `medsumctl seed-dev`, `dataset.load`, AI service | none; `seed-dev` runs only with `MEDSUM_ENV=dev`, and only then may the AI service and the eval runner fall back to the local database |
| `MEDSUM_EVAL_ADMIN_URL` | `evals.run` (required unless `MEDSUM_ENV=dev`) | none; a connection to the server where the eval runner creates and drops its throwaway database. With `MEDSUM_ENV=dev`, the local server as `postgres` |
| `MEDSUM_PRIMARY_MODEL` | AI service | `qwen3.5:4b` |
| `MEDSUM_FALLBACK_MODEL` | AI service | `gemma4:e2b` |
| `MEDSUM_NUM_CTX` | AI service | `16384` (context window for each model call) |
| `MEDSUM_WORKER` | AI service | `1`; `0` starts the service without its document worker (nothing queued is processed) |
| `MEDSUM_REPAIR`, `MEDSUM_REPAIR_TOOL_TURNS` | AI service | `fixed`, `3`; `tools` lets a repair read the document with read-only tools first, within that many model turns per repair (the fix's included) |
| `MEDSUM_BREAKER_FAILURES`, `MEDSUM_BREAKER_COOLDOWN_S` | AI service | `3`, `900`; consecutive model errors or timeouts that disable a model, and how long it stays disabled before it is tried again |
| `MEDSUM_AUDIT_SAMPLE_RATE` | AI service | `0.10`; the share of verified documents sent back to a clinician as an audit (0 to 1) |
| `MEDSUM_JOB_LEASE`, `MEDSUM_JOB_HEARTBEAT_S`, `MEDSUM_JOB_MAX_ATTEMPTS` | AI service | `5 minutes`, `60`, `3`; how long a claim lasts, how often it is renewed, and how many claims a job gets before it is set aside |
| `MEDSUM_LOG_FILE` | AI service logs (`observability`) | `scripts/logs/medsum.jsonl`; JSON log lines (ids, events, timings) without patient content, also on stderr. Set up when the service starts |
| `VITE_API_BASE_URL` | Frontend | `/api`, set in `frontend/.env.local`; the Vite dev server proxies it to the backend |

The AI service reads its database settings and its token in one place, `scripts/src/settings.py`, and checks them when it starts: outside `MEDSUM_ENV=dev`, a missing `DATABASE_URL` stops it at start-up instead of connecting somewhere by default, and a missing or short `INTERNAL_API_TOKEN` stops it everywhere. The Go server checks its own in `cmd/server/config.go`.

### Database roles

The development setup connects as `postgres`, a superuser. Anywhere else, give the AI service its own role, `medsum_ai`, with only the privileges it uses ([`db/ai_service_role.sql`](db/ai_service_role.sql)). It can read documents and write their transcription, read and write processing state (insights, jobs, records, pipeline runs and steps, model calls) and its checkpoints, and read the patient details the checks and the summary need, plus the uploader's name. It cannot read emails, password hashes, sessions or setup links, delete or truncate rows, or create, alter or drop tables in `public`. Create it once per database, as a superuser, after loading the schema, then give it a password:

```bash
psql -U postgres -d med_sum -f db/ai_service_role.sql
psql -U postgres -d med_sum -c "\password medsum_ai"
```

Then start the AI service with `DATABASE_URL="postgres://medsum_ai:<password>@localhost:5432/med_sum?sslmode=disable"`. The script is safe to run again, and `python -m reset_db` runs it after a reset when the role exists, since dropping the tables drops their grants. `scripts/tests/test_ai_service_role.py` runs a document job and a patient summary as the role and checks what it is refused.

The Go API has its own role, `medsum_api` ([`db/api_role.sql`](db/api_role.sql)), created and given a password the same way:

```bash
psql -U postgres -d med_sum -f db/api_role.sql
psql -U postgres -d med_sum -c "\password medsum_api"
```

It reads and writes accounts, sessions, links, patients and documents, adds and reads audit rows, and reads the AI service's results. It can't delete a patient, a document or an account (deleting a document in the app is an update), change or delete an audit row, change an organisation, read the jobs, prompts or pipeline tables, or create, alter or drop a table. `reset_db` grants it again after a reset when it exists, and `backend/internal/db/role_test.go` runs every query the API makes as the role and checks what it is refused. The admin tools (`reset_db`, `medsumctl`) connect as the schema's owner.
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
| `GET` | `/admin/audit?actor=&patient=&from=&to=&before=` | Your organisation's audit log, newest first, 200 entries a page (`next_before` gives the next page); `from` and `to` are dates, both inclusive (admins) |
| `GET` | `/patients?q=` | Your organisation's patients (up to 200), by part of the name or the start of the MRN; no `q` lists them all |
| `POST` | `/patients` | Add a patient `{mrn, full_name, date_of_birth?, age_years?, sex?}` (`date_of_birth` as `YYYY-MM-DD`, or `age_years` when it isn't known, not both; `sex` is `F`, `M` or `O`). 409 when the MRN is taken |
| `GET` | `/patients/{id}` | One patient |
| `PATCH` | `/patients/{id}` | Change any of those fields; `null` clears an optional one |
| `GET` | `/patients/{id}/documents` | The patient's documents, newest first, each with its `insight_status`, uploader and `duplicate_of` |
| `POST` | `/patients/{id}/documents` | Upload a PDF, JPG or PNG (multipart field `file`, max 20 MB) and start processing it. The same file uploaded again for the same patient is accepted and names the first as `duplicate_of`; the patient summary counts it once |
| `GET` | `/patients/{id}/summary` | The patient summary and its status (`none`, `processing`, `completed`, `failed`) |
| `POST` | `/patients/{id}/summary` | Start generating the patient summary |
| `GET` | `/documents/{id}` | One document: name, patient, uploader, status |
| `DELETE` | `/documents/{id}` | Delete a document you uploaded (403 for anyone else's). It disappears from the patient's record and the summary is rebuilt; its rows and file are kept |
| `GET` | `/documents/{id}/insight` | Get the summary and its status (`not_started`, `processing`, `verified`, `needs_review`, `failed`) |
| `POST` | `/documents/{id}/insight` | Start or retry processing |
| `GET` | `/documents/{id}/record` | The document's structured record as stored: every printed and derived value, the page transcriptions, and each item's provenance. `citations` lists every value's page, lines and quoted text with its item's review status (`validated`, `low_confidence`, ...) and the reasons it was flagged. 404 before the document has a record |
| `POST` | `/documents/{id}/reprocess` | Process the document again from the start, as a new run |
| `GET` | `/documents/{id}/file` | The original upload, for checking values against it (PDF, JPEG or PNG, by the type the upload checked). The one response the app may frame (its CSP names the app's origins in `frame-ancestors`); viewing it is audited |
| `GET` | `/documents/{id}/processing` | How the document's latest pipeline run went, step by step: status, model and its role, duration, attempt, model calls and the checks that failed each step (validator, severity, item and field, never the message or any value); viewing it is audited |
| `POST` | `/documents/{id}/review` | A clinician's review of one value: `{action: "confirm", item_id}` or `{action: "correct", item_id, field, value, note?}`, with the `record_updated_at` the page loaded (409 when the record changed since, or while the document is processed). The model's reading is kept beside every correction. A corrected value that fails a value check (units, flag against range, dates) answers 422 with its `findings` and is saved when sent again with `accept_findings: true`. When nothing awaits review the document is verified and its patient summary rebuilt |
| `GET` | `/review/queue` | Your organisation's documents awaiting review, longest waiting first, with how many values each has flagged; verified documents sampled for an audit are listed too, with `reason: "audit"` |
| `POST` | `/documents/{id}/audit-complete` | Finish the audit of a verified document sampled for one, once every value (the document's details, the patient's and each item) has been confirmed or corrected during the audit (409 until then, and when it is not waiting for an audit); it leaves the queue. Corrections made during the audit count as errors the pipeline let through |

Patient, document and summary routes need the clinician role and `/admin/*` the admin role; without it the answer is 403. Patients, documents and accounts in another organisation are answered with 404.

The AI service exposes `GET /health` (with each model's circuit-breaker state) and four internal routes: `POST /internal/generate-insights` and `POST /internal/reprocess-document` queue a document, `POST /internal/generate-patient-summary` starts a patient's summary, and `POST /internal/review-apply` applies a clinician's review to a record the API sends it (it reads and writes nothing; the API stores the result with its audit row). All four require an `X-Internal-Token` header.

A Postman collection is in [`postman/collections/`](postman/collections/med-sum.postman_collection.json). It uses the variables `backend_url`, `ai_service_url`, `email`, `password` and `internal_api_token` (your `.env.local` value; the collection holds none). Log in first: Postman keeps the `medsum_session` cookie from that response and sends it with every later request, and each request that changes something carries `X-Requested-With: med-sum`.

## MCP server

A local [MCP](https://modelcontextprotocol.io) server lets an MCP client (a desktop assistant, an IDE, an agent) read the records with the same read-only tools the repair step uses. It never connects to the database: it reads through the API, logged in as its own clinician account, so the account's organisation, its permissions and the audit log apply to every call. See [ADR 0012](docs/adr/0012-shared-tool-layer.md).

> **Synthetic data only.** Whatever a tool returns goes to the client's model, and many clients run their model at a cloud provider. Real patient data must never be served to one. The server starts only with `MEDSUM_ENV=dev`, where every document is synthetic (the [synthetic dataset](#synthetic-dataset), loaded with `python -m dataset.load --yes`).

| Tool | Reads | Answers with |
|---|---|---|
| `find_patients` | `GET /patients?q=` | patients of the organisation by part of the name or the start of the MRN (at most 25) |
| `list_documents` | `GET /patients/{id}/documents` | a patient's documents, newest first, with their processing status |
| `list_items` | `GET /documents/{id}/record` | the record's items (lab results, medicines, conditions, ...), each with its id and citation |
| `get_item` | `GET /documents/{id}/record` | one item as parsed, as clinicians reviewed it, with what code derived and the text of the lines it cites |
| `search_transcription` | `GET /documents/{id}/record` | the transcription's lines that contain some text (at most 12) |
| `get_lines` | `GET /documents/{id}/record` | lines of one page of the transcription (at most 40) |
| `get_text_layer` | `GET /documents/{id}/record` | the PDF's own text of one page, which the transcription is checked against |

Every answer is `{"citations": [...], "result": {...}}`, and every entry of the result carries its own `cite`: `patient:<id>`, `document:<id>`, `document:<id>#p2:7-9` (transcription lines) or `document:<id>#text-layer:p3:1-40`. No value comes without the place it was read from. Each tool call is one API request, so one audit row, and the row's details name the tool (the server sends `X-Medsum-Tool`). Admins see the rows on the Audit log page, under the server's account. A call whose arguments don't fit the tool's schema is answered before any request is made and reads nothing.

**Set it up.** Create an account for the server alone, so its reads are told apart from a person's, and set its password with the link `medsumctl` prints:

```bash
cd backend
go run ./cmd/medsumctl create-user --email mcp-client@med-sum.test --name "MCP client"
```

Then copy `scripts/mcp.env.example` to `scripts/mcp.env.local` (untracked) and fill in `MEDSUM_MCP_EMAIL` and `MEDSUM_MCP_PASSWORD`. `MEDSUM_MCP_API_URL` defaults to `http://localhost:8080`, and `MEDSUM_ENV=dev` must stay. A variable set in the environment wins over the file. When the session ends (idle or absolute timeout, a revoke, a new password), the server logs in again.

**Connect a client.** The server speaks MCP over stdio: the client starts it as a subprocess. Most clients take a JSON entry like this one, with your clone's paths (on macOS or Linux, the interpreter is `scripts/.venv/bin/python`):

```json
{
  "mcpServers": {
    "medsum": {
      "command": "D:/path/to/med-sum/scripts/.venv/Scripts/python.exe",
      "args": ["-m", "medsum_mcp"],
      "env": { "PYTHONPATH": "D:/path/to/med-sum/scripts/src" }
    }
  }
}
```

The API must be running. To try the server by hand, run `python -m medsum_mcp` from `scripts/src`, or point the MCP Inspector at that command. The server's log lines go to stderr and hold the tool, the outcome, the status and the time, never an argument or a value.

**Measure a client.** `python -m evals.mcp_eval --live` asks a local model questions about the synthetic patients, answered through the server, and scores each answer (see [Evals](#evals)). It has not been run yet.

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
db/ai_service_role.sql   the AI service's least-privilege role (see Database roles)
db/api_role.sql          the Go API's least-privilege role
docs/adr/                architecture decision records: why the system is built this way
frontend/
  src/api/               API client
  src/components/        shared components (sandboxed HTML viewer, record view and source page, sessions list, idle warning)
  src/record/            what the record view shows: values, derived values, citations (with tests)
  src/auth/              the logged-in account (AuthProvider), permissions and the route guard
  src/pages/             login, set password, account, staff, audit log, patients, patient, document, patient summary
scripts/
  src/insights_service.py    FastAPI service; starts the document worker
  src/document_jobs.py       the document job queue: claims, leases, the worker
  src/settings.py            database settings, checked at start-up (no default outside dev)
  src/pipeline/              the document graph: nodes, routing, low-confidence signals, checkpoints, step replay
  src/views/                 HTML built by code from records: the document page and the patient summary
  src/summary/               the patient summary: facts from the records, the overview and its check
  src/tools/                 read-only tools over one document (lines, search, text layer, record items), typed and citing where each answer is from
  src/medsum_mcp/            the stdio MCP server over those tools, reading through the Go API
  src/model_health.py        the circuit breaker per model
  src/llm.py                 model calls with the primary and fallback model
  src/record/                the structured record of a document and the schemas the model fills in
  src/derive/                values computed from the printed text: numbers, ranges, flags, unit table, dose schedules, dates
  src/validators/            deterministic checks of a record against its transcription and PDF text
  src/observability/         pipeline runs and steps in the database, run versions, provenance checks, JSON logs
  src/reset_db.py            resets the development database and uploads
  schemas/                   those schemas exported as JSON (regenerate: `python -m record.schemas` in src/)
  src/dataset/               synthetic dataset generator and loader
  src/evals/                 eval runner, scoring against the answer keys, fault check, model choice, reports
  evals/dataset/             the generated dataset: fictional documents, answer keys, transcriptions
  evals/faultcheck_baseline.json   the fault check's committed detection counts (its gate)
  evals/repair_cases.json    the repair eval's cases: one injected fault each (`python -m evals.repair_cases`)
  evals/history/             one small JSON per live eval run (counts only), compared between runs; written by the first live run
  evals/reports/             the full report of every live eval run, under the same name as its history entry
  evals/cassettes/           recorded model replies, so an eval can be replayed without Ollama; written by the first live run
  prompt/                    prompts: page transcription, classify, parse per type, repair, the summary's overview
  tests/                 pytest suite (fixtures in conftest.py)
  mcp.env.example        the MCP server's settings; copy to mcp.env.local (untracked)
  requirements.in        direct dependencies; requirements.txt pins every package
  requirements-dev.in    test tools; requirements-dev.txt pins them
postman/                 API collection
```

## Development

The frontend's tests (`npm test`) use Node's own test runner on the functions in `src/record/`, with a synthetic record that a pytest test keeps valid against the record schema.

Install the Python test tools (pytest, [Ruff](https://docs.astral.sh/ruff/) and Beautiful Soup, which the tests use to read the generated pages) into the AI service's virtual environment:

```bash
cd scripts && pip install -r requirements.txt -r requirements-dev.txt
```

Checks and tests for each part:

```bash
(cd backend  && go build ./... && go vet ./... && go test ./...)
(cd scripts  && ruff check src tests && ruff format --check src tests && pytest)
(cd frontend && npm run lint && npm test && npm run build)
```

The tests never call a model: model calls are replaced with fakes, so Ollama need not be running. The database tests create a throwaway database (`med_sum_test_go` for Go, `med_sum_test_py` for Python) from `db/schema.sql`, and drop it afterwards; your `med_sum` database is not touched. The Go tests need `psql` on the `PATH`. Both connect as `postgres:postgres@localhost:5432` unless `MEDSUM_TEST_ADMIN_URL` says otherwise. When Postgres can't be reached they are skipped; set `MEDSUM_REQUIRE_DB=1` to make that a failure. `pytest -m "not db"` runs only the tests that need no database.

### Synthetic dataset

[`scripts/evals/dataset/`](scripts/evals/dataset/README.md) holds 25 fictional documents (lab reports and prescriptions for 6 patients) with an answer key and expected transcription for each. Regenerate it with `python -m dataset.build` from `scripts/src`. `python -m dataset.load` shows what it would upload through the backend; `--yes` uploads it, which starts processing on the AI service (live model runs). It logs in as the development clinician, creates each dataset patient's record from the manifest (or finds it by MRN) and uploads that patient's documents to it, so run `medsumctl seed-dev` first and set `MEDSUM_DEV_PASSWORD`.

Each document in the manifest also says how it should end (`expected_end_state`, and the routing signals it is built to raise: an undated prescription, a report printed for another patient, an unsupported document type) and which split it belongs to. d02 and d09 are `dev`: every prompt check so far ran on them. d18-d24 are `held_out`: two further patients, printed in layouts the other documents don't use (results as lines rather than a table, patient details in a key-value table or several to a line, the range before the unit, flags as words or arrows, two columns, numbered medicine lines, the strength in its own column, a table continued on the next page, dates with the month in words). No prompt or check is changed because of them; they show whether the numbers hold on layouts the pipeline wasn't built on. The other 16 are `test`, and reports show the three apart. One of them, d25, is a 60-result check-up with 27 results on each of its first two pages, so each of those pages is parsed in two parts. Its fourth page is the laboratory's test menu in small print, built to run past a page call's output cap (about 5,200 tokens), so it is read again in halves. The held-out keys first showed three reading gaps: "Glycated Haemoglobin (HbA1c)" and "TSH (Ultrasensitive)" weren't recognised as HbA1c and TSH, and "Allergy: None known" wasn't read as no known allergies. They were closed as general rules, each with test cases of its own on other names and phrasings (a trailing qualifier in brackets is ignored when it can't name a different test; more ways of printing "no known allergies"), and a test lists any remaining difference between the code and the held-out keys (none today).

### Evals

All of these run from `scripts/src`.

- **`python -m evals.faultcheck`** measures the checks themselves, with no model. It takes each answer key, injects one fault at a time (a digit swapped, a decimal point moved, a unit swapped, a range bound taken for the value, a dropped row or medicine, an invented result or medicine, a remark moved to another test, a changed strength or frequency, a day and month swapped, the document uploaded to another patient, a value misread in the transcription itself), runs the validators and signals on the altered copy, and counts the faults that raise a routing signal. It runs in a few seconds. It fails when a clean document ends anywhere but where the manifest expects, or when a fault is caught less often than the committed baseline. It caught 962 of 988 faults. Every fault in a value, unit, strength, frequency or date was caught, and so were range bounds taken for the value, invented items, dropped medicines and wrong patients, and 161 of 167 dropped lab results. What gets through is listed under Limitations.
- **`python -m evals.run --live`** runs every document through the real graph, with each model as the primary, in a throwaway database (`med_sum_evals`). It scores each record and transcription against the answer key and checks each end state against the manifest. The report gives field precision and recall, unsafe errors, transcription accuracy and routing. It also gives the **risk-coverage line**: the share of documents that ended `verified` (no clinician looks at them) and the unsafe errors inside that share. Every count carries a 95% Wilson interval, because 25 documents can't support a bare percentage (the self-check below shows how wide the interval is). A live run of the whole dataset takes hours on a CPU. It records every model reply as a cassette. Each live run is kept in the repository: its full report (`report.md` to read, `report.json` with every document's scores and each wrong, missed or invented value) goes to [`scripts/evals/reports/`](scripts/evals/reports/README.md), and an entry with its counts and what produced it (code commit, prompt hashes, model digests) to `scripts/evals/history/`, both under the same name. A replay, or a live run with `--no-history`, writes its report to the gitignored `scripts/evals/results/` instead.
- **`python -m evals.run --replay`** runs the same graph from the cassettes, without Ollama. A changed prompt, page, option or output schema is a miss, and the run stops with a message naming what changed and asking for a re-record.
- **`python -m evals.repair --live`** compares the two repairs on the same parses. A clean dataset document rarely needs a repair, so its cases are made by injecting one fault into an answer key's parse (`scripts/evals/repair_cases.json`: 676 cases over 11 kinds of fault; `--subset core` takes one per fault from the test documents, `extended` adds one per fault from the held-out ones). Each case runs through the graph's own repair and validate nodes with each model and each repair. The report gives cases repaired (with an interval), model calls, tokens and seconds per repair, unsafe errors left after repair, and for the tool repair every tool call: by tool, malformed, unknown, not found. It says which repair the numbers support: the tool repair only if it repairs at least as many cases, leaves no more unsafe errors and is not slower per repair. `--replay` answers from its cassettes.
- **`python -m evals.toolprobe --live`** checks that each model can call the tools through Ollama at all: seven short text-only cases over an invented report, scored for the right tool, well-formed arguments, the right page and lines, and the answer given after reading.
- **`python -m evals.compare A B`** compares two history entries or reports. It fails when unsafe errors rise (overall or in verified documents), when routing or medicine recall falls, or when field F1 or value recall falls by more than a point. **`python -m evals.history`** prints every run as a table.
- **`python -m evals.review_signals`** measures the signals in use, from clinicians' reviews in the database: for each low-confidence signal, the values it raised and the share a clinician corrected (its precision), and from the audit samples, the share of verified documents that still held a value a clinician corrected (the escape rate). Counts only, with intervals. **`python -m evals.export_review <document_id> <out_dir>`** writes a reviewed document as a candidate eval case (the file, a key from the reviewed record, and which fields were corrected), to be checked by hand before it joins the dataset; development only (`MEDSUM_ENV=dev`).
- **`python -m evals.mcp_eval --live`** measures an MCP client: questions about the synthetic patients, built from the answer keys, go to a local model that can read the records only through the [MCP server](#mcp-server). Value questions (a lab result, a medicine's strength or frequency) are scored for quoting a citation, every citation covering lines a tool returned, every number appearing in what the tools returned (the overview's number check), pointing at the key's lines in the right document, and holding the key's value. Questions about a test none of the patient's documents holds are scored for abstaining. Each measure carries its interval. It needs the dataset loaded and processed and the API running; it has not been run yet.
- **`python -m evals.selfcheck`** scores the answer keys against themselves to show the report's shape. Every score is perfect, and 22 of 25 documents end verified. Even so, its 0 unsafe errors in 204 clinical items still allows up to 1.8% at 95%: that is how little a dataset this size can rule out, and why every count in the report carries its interval.
- **`python -m evals.redaction`** measures how well patient identifiers are masked in what leaves the clinical stores (findings, errors, logs): recall per identifier type, with the run's known values and with patterns alone. See [where patient data goes](docs/data-and-threats.md).

## Limitations

- **Access control is built for one hospital on one machine.** Accounts, sessions, roles, staff management, patient records shared within an organisation, the audit log and the hardening above work. What it leaves out: medical registration numbers are not verified; there is no email, so setup and reset links are handed over by an admin; there is no second factor; every clinician of an organisation sees every patient of it (no per-patient or per-department restrictions, and no break-glass access); the first admin of an organisation is created from the command line; uploads and the database are not encrypted at rest; TLS is the deployer's job; the login limit is per API process; and a failed login with an unknown email is recorded without an organisation, so no admin sees it on the Audit log page.
- **Vision misreads are only caught where a PDF has a text layer.** A scan or photo has no independent text to compare the model's reading with, so a value read wrongly but consistently passes the checks. With a text layer, each value is looked for in its own row (next to its test or medicine name), with the row's own reference range taken out, so a misread that matches a number in another row, or a bound of its own range, is still caught; one that matches a number in its test name or unit passes. The fault check caught 110 of 130 such misreads, and all 20 it missed were on pages without a text layer.
- **A page too long for one call is joined only when its halves agree exactly.** A capped page is read again as a top and a bottom half that overlap, and joined only on a run of identical lines found in one place. A page in two columns can't be joined that way (each half reads part of one column, then part of the other), and neither can long paragraphs, which the model writes as single long lines that rarely come out the same twice. Such a page keeps no lines and goes to review.
- **Some wrong values pass the checks.** Every row of a results table must become a result, so a row the parse leaves out goes back to the parse, then to review. A result printed as a line of text rather than a table row has no such check: leaving it out is only an information note, so the document can still end verified (the 6 dropped results the fault check missed are all of this kind). A value equal to one of its own range bounds is sent to review when its row prints that number only inside the range (98 of 98 range bounds taken for the value were caught); one printed in its own cell as well is taken as the real value.
- **No medical codes or brand-to-generic mapping.** Medicines are kept as written (brands stay brands), and no LOINC, SNOMED or RxNorm codes are assigned: the local models aren't reliable enough to map them.
- **Lab reports and prescriptions only.** Any other document is kept, transcribed and marked for review, but not parsed.
- **Not yet evaluated on the models.** The eval runner, the repair eval, the MCP client eval, the fault check and the synthetic dataset exist, and the checks have been measured, but the pipeline has not yet been run live over the dataset on both models, so neither repair has been measured. A short probe showed both models calling a tool correctly. Signal precision and the escape rate need clinicians' reviews, so they have no numbers yet either.
- **Clinicians' licences are not verified.** A medical registration number can be recorded on an account, but nothing checks it against a medical council's register.
- **Not validated clinically.** Nothing here has been tried by clinicians or on real documents. The synthetic dataset is varied in layout, but it was built by the same hands as the checks.
- **The overview check is strict and literal.** It compares words, not meanings. A number counts as in context when one of the words around it (three either side) is a word of what it belongs to, so two tests that share a unit in one list can still trade values, and an ordinary word missing from its fixed list leaves the overview out. Its layout is a first draft.
- **No container setup** yet, and no CI.

## Roadmap

The next phase focuses on doctors and hospital groups:

- Live eval runs on both models, reported for the dev, test and held-out documents apart, which fill in the [results](#results)
- A prompt-injection eval: documents carrying embedded instructions, hidden text and remote images, scored for a faithful record and contained pages
- A trace screen showing each run's steps, inputs, outputs, retries and models
- CI on every push (builds, linters, tests, the fault check, eval replay from recorded replies) and the one-minute demo
- A clinical pilot on a hospital's own documents, after the deployment work under [For hospitals](#for-hospitals)

## License

[MIT](LICENSE)
