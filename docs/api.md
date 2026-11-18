# API and MCP server

The Go API's routes, the AI service's internal routes, and the local MCP server that reads the records through the API.

## REST API


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
| `GET` | `/admin/runs?before=` | Your organisation's pipeline runs, newest first, 200 a page (`next_before` gives the next page): graph version, start and end, end state, models, code version, step counts. No document, patient or content (admins) |
| `GET` | `/admin/runs/{id}` | One run and its steps: status, model and role, timings, tokens, prompt name, the checks that failed each (validator, severity, item and field, never the message) and a `replay_command` where it can be replayed (admins) |
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
| `GET` | `/documents/{id}/record` | The document's structured record as stored: every printed and derived value, the page transcriptions, and each item's provenance. `citations` lists every value's page, lines and quoted text with its item's review status (`validated`, `low_confidence`, ...) and the reasons it was flagged. With `?view=reviewed` it also returns `reviewed`, the record with every clinician's correction applied and each corrected item's derived values computed again (by the AI service; a record without corrections is its own view), which the document page shows; 503 when that can't be computed, with nothing sent and no audit row. 404 before the document has a record |
| `POST` | `/documents/{id}/reprocess` | Process the document again from the start, as a new run |
| `GET` | `/documents/{id}/file` | The original upload, for checking values against it (PDF, JPEG or PNG, by the type the upload checked). The one response the app may frame (its CSP names the app's origins in `frame-ancestors`); viewing it is audited |
| `GET` | `/documents/{id}/processing` | How the document's latest pipeline run went, step by step: status, model and its role, duration, attempt, model calls and the checks that failed each step (validator, severity, item and field, never the message or any value); viewing it is audited |
| `GET` | `/documents/{id}/trace?run=` | The document's full trace: its runs, newest first, and one run in full (the latest, or the `run` given): each step's input, output, findings with their messages, error, tokens and prompt, the prompt texts by hash, and a `replay_command` for each step that can be replayed. 404 for a run of another document; viewing it is audited |
| `POST` | `/documents/{id}/review` | A clinician's review of one value: `{action: "confirm", item_id}` or `{action: "correct", item_id, field, value, note?}`, with the `record_updated_at` the page loaded (409 when the record changed since, or while the document is processed). The model's reading is kept beside every correction. A corrected value that fails a value check (units, flag against range, dates) answers 422 with its `findings` and is saved when sent again with `accept_findings: true`. When nothing awaits review the document is verified and its patient summary rebuilt |
| `GET` | `/review/queue` | Your organisation's documents awaiting review, longest waiting first, with how many values each has flagged; verified documents sampled for an audit are listed too, with `reason: "audit"` |
| `POST` | `/documents/{id}/audit-complete` | Finish the audit of a verified document sampled for one, once every value (the document's details, the patient's and each item) has been confirmed or corrected during the audit (409 until then, and when it is not waiting for an audit); it leaves the queue. Corrections made during the audit count as errors the pipeline let through |

Patient, document and summary routes need the clinician role and `/admin/*` the admin role; without it the answer is 403. Patients, documents and accounts in another organisation are answered with 404.

The AI service exposes `GET /health` (with each model's circuit-breaker state) and five internal routes: `POST /internal/generate-insights` and `POST /internal/reprocess-document` queue a document, `POST /internal/generate-patient-summary` starts a patient's summary, and `POST /internal/review-apply` applies a clinician's review to a record the API sends it (it reads and writes nothing; the API stores the result with its audit row), and `POST /internal/reviewed-view` returns a record the API sends it with its corrections applied (also a pure function). All five require an `X-Internal-Token` header.

A Postman collection is in [`postman/collections/`](../postman/collections/med-sum.postman_collection.json). It uses the variables `backend_url`, `ai_service_url`, `email`, `password` and `internal_api_token` (your `.env.local` value; the collection holds none). Log in first: Postman keeps the `medsum_session` cookie from that response and sends it with every later request, and each request that changes something carries `X-Requested-With: med-sum`.

## MCP server

A local [MCP](https://modelcontextprotocol.io) server lets an MCP client (a desktop assistant, an IDE, an agent) read the records with the same read-only tools the repair step uses. It never connects to the database: it reads through the API, logged in as its own clinician account, so the account's organisation, its permissions and the audit log apply to every call. See [ADR 0012](adr/0012-shared-tool-layer.md).

> **Synthetic data only.** Whatever a tool returns goes to the client's model, and many clients run their model at a cloud provider. Real patient data must never be served to one. The server starts only with `MEDSUM_ENV=dev`, where every document is synthetic (the [synthetic dataset](development.md#synthetic-dataset), loaded with `python -m dataset.load --yes`).

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

**Measure a client.** `python -m evals.mcp_eval --live` asks a local model questions about the synthetic patients, answered through the server, and scores each answer (see [Evals](development.md#evals)). It has not been run yet.

