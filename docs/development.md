# Development

The layout of the repository, the checks and tests, the synthetic dataset and the evals.

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
  src/api/               API client, and the request code it uses (with tests)
  src/components/        shared components (sandboxed HTML viewer, record view and source page, processing and trace panels, sessions list, idle warning)
  src/record/            what the record view, processing and trace panels show: values, derived values, citations, steps (with tests)
  src/auth/              the logged-in account (AuthProvider), permissions, the route guard, and the login links (with tests)
  src/pages/             login, set password, account, staff, audit log, pipeline runs, patients, patient, document, patient summary, review queue
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
  src/evals/                 eval runner, scoring against the answer keys, fault check, injection eval, model choice, reports
  src/demo/                  `python -m demo`: two documents through the real graph from recorded replies
  evals/dataset/             the generated dataset: fictional documents, answer keys, transcriptions
  evals/injection/           attacked copies of six dataset documents, the injection eval's own split
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
.github/workflows/ci.yml  CI: every check below, on each push to main and each pull request
```

## Checks and tests

The frontend's tests (`npm test`) use Node's own test runner on the functions in `src/record/`, with a synthetic record that a pytest test keeps valid against the record schema, and on the request code (`src/api/request.js`: the CSRF header, the 401 and session-expiry events, review findings, with `fetch` stubbed) and the login links (`src/auth/links.js`: where `?next=` may lead, the set-password token).

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

**CI** ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) runs the same checks on GitHub Actions on each push to `main` and each pull request, with a Postgres service so the database tests run rather than skip, plus the fault check and `npm audit --omit=dev --audit-level=high`, which fails on a high or critical advisory in the frontend's runtime dependencies. A fourth job replays the evals (the main set and the injection set) from the recorded replies and compares the result with the last live run; it is skipped until the first live run has recorded them. No job calls a model or holds a secret.

### Synthetic dataset

[`scripts/evals/dataset/`](../scripts/evals/dataset/README.md) holds 25 fictional documents (lab reports and prescriptions for 6 patients) with an answer key and expected transcription for each. Regenerate it with `python -m dataset.build` from `scripts/src`. `python -m dataset.load` shows what it would upload through the backend; `--yes` uploads it, which starts processing on the AI service (live model runs). It logs in as the development clinician, creates each dataset patient's record from the manifest (or finds it by MRN) and uploads that patient's documents to it, so run `medsumctl seed-dev` first and set `MEDSUM_DEV_PASSWORD`.

Each document in the manifest also says how it should end (`expected_end_state`, and the routing signals it is built to raise: an undated prescription, a report printed for another patient, an unsupported document type) and which split it belongs to. d02 and d09 are `dev`: every prompt check so far ran on them. d18-d24 are `held_out`: two further patients, printed in layouts the other documents don't use (results as lines rather than a table, patient details in a key-value table or several to a line, the range before the unit, flags as words or arrows, two columns, numbered medicine lines, the strength in its own column, a table continued on the next page, dates with the month in words). No prompt or check is changed because of them; they show whether the numbers hold on layouts the pipeline wasn't built on. The other 16 are `test`, and reports show the three apart. One of them, d25, is a 60-result check-up with 27 results on each of its first two pages, so each of those pages is parsed in two parts. Its fourth page is the laboratory's test menu in small print, built to run past a page call's output cap (about 5,200 tokens), so it is read again in halves. The held-out keys first showed three reading gaps: "Glycated Haemoglobin (HbA1c)" and "TSH (Ultrasensitive)" weren't recognised as HbA1c and TSH, and "Allergy: None known" wasn't read as no known allergies. They were closed as general rules, each with test cases of its own on other names and phrasings (a trailing qualifier in brackets is ignored when it names only a method or a blood specimen; more ways of printing "no known allergies"), and a test lists any remaining difference between the code and the held-out keys (none today).

### Evals

All of these run from `scripts/src`.

- **`python -m evals.faultcheck`** measures the checks themselves, with no model. It takes each answer key, injects one fault at a time (a digit swapped, a decimal point moved, a unit swapped, a range bound taken for the value, a "<" or "Non-" dropped from the value, two rows' values swapped under one citation, a dropped row or medicine, an invented result or medicine, a result or medicine parsed twice, a remark moved to another test, a changed strength or frequency, a day and month swapped, the document uploaded to another patient, a value misread in the transcription itself), runs the validators and signals on the altered copy, and counts the faults that raise a routing signal. It runs in a few seconds. It fails when a clean document ends anywhere but where the manifest expects, or when a fault is caught less often than the committed baseline. It caught 1104 of 1130 faults. Every fault in a value, unit, strength, frequency or date was caught, and so were range bounds taken for the value, dropped comparators, swapped rows, invented and duplicated items, dropped medicines and wrong patients, and 161 of 167 dropped lab results. What gets through is listed under [Limitations](limitations.md).
- **`python -m evals.run --live`** runs every document through the real graph, with each model as the primary, in a throwaway database (`med_sum_evals`). It scores each record and transcription against the answer key and checks each end state against the manifest. The report gives field precision and recall, unsafe errors, transcription accuracy and routing. An unsafe error is a wrong or invented value a clinician could act on: an invented result, medicine or allergy, or a wrong test name, value, unit, flag, medicine name, strength, dose, frequency, allergen or document date. Missing values count against recall instead. The interval is over items: items with an unsafe error out of the items checked (the key's clinical items, invented ones and each document's dates), so it can't pass 100%. It also gives the **risk-coverage line**: the share of documents that ended `verified` (no clinician looks at them) and the unsafe errors inside that share. Every count carries a 95% Wilson interval, because 25 documents can't support a bare percentage (the self-check below shows how wide the interval is). A live run of the whole dataset takes hours on a CPU. It records every model reply as a cassette. Each live run is kept in the repository: its full report (`report.md` to read, `report.json` with every document's scores and each wrong, missed or invented value) goes to [`scripts/evals/reports/`](../scripts/evals/reports/README.md), and an entry with its counts and what produced it (code commit, prompt hashes, model digests, generation options with the seed) to `scripts/evals/history/`, both under the same name. A replay, or a live run with `--no-history`, writes its report to the gitignored `scripts/evals/results/` instead.
- **`python -m evals.run --replay`** runs the same graph from the cassettes, without Ollama. A changed prompt, page, option or output schema is a miss, and the run stops with a message naming what changed and asking for a re-record.
- **`python -m evals.repair --live`** compares the two repairs on the same parses. A clean dataset document rarely needs a repair, so its cases are made by injecting one fault into an answer key's parse (`scripts/evals/repair_cases.json`: 676 cases over 11 kinds of fault; `--subset core` takes one per fault from the test documents, `extended` adds one per fault from the held-out ones). Each case runs through the graph's own repair and validate nodes with each model and each repair. The report gives cases repaired (with an interval), model calls, tokens and seconds per repair, unsafe errors left after repair, and for the tool repair every tool call: by tool, malformed, unknown, not found. It says which repair the numbers support: the tool repair only if it repairs at least as many cases, leaves no more unsafe errors and is not slower per repair. `--replay` answers from its cassettes.
- **`python -m evals.toolprobe --live`** checks that each model can call the tools through Ollama at all: seven short text-only cases over an invented report, scored for the right tool, well-formed arguments, the right page and lines, and the answer given after reading.
- **`python -m evals.compare A B`** compares two history entries or reports. It refuses (exit 2) runs that aren't alike: another dataset, another set of models, or a model run on other documents (so another split). Otherwise it fails when unsafe errors rise (overall or in verified documents), when routing or medicine recall falls, or when field F1 or value recall falls by more than a point. **`python -m evals.history`** prints every run as a table.
- **`python -m evals.review_signals`** measures the signals in use, from clinicians' reviews in the database: for each low-confidence signal, the values it raised and the share a clinician corrected (its precision), and from the audit samples, the share of verified documents that still held a value a clinician corrected (the escape rate). Counts only, with intervals. **`python -m evals.export_review <document_id> <out_dir>`** writes a reviewed document as a candidate eval case (the file, a key from the reviewed record, and which fields were corrected), to be checked by hand before it joins the dataset; development only (`MEDSUM_ENV=dev`).
- **`python -m evals.mcp_eval --live`** measures an MCP client: questions about the synthetic patients, built from the answer keys, go to a local model that can read the records only through the [MCP server](api.md#mcp-server). Value questions (a lab result, a medicine's strength or frequency) are scored for quoting a citation, every citation covering lines a tool returned, every number appearing in what the tools returned (the overview's number check), pointing at the key's lines in the right document with a narrow citation (at most two lines wider than the key's), and holding the key's value as a whole word ("132" isn't in "1132"), both in the answer and in what a tool returned for the cited lines. Questions about a test none of the patient's documents holds are scored for abstaining. Each measure carries its interval. It needs the dataset loaded and processed and the API running; it has not been run yet.
- **`python -m evals.injection --live`** runs the attacked documents in [`scripts/evals/injection/`](../scripts/evals/injection/README.md) through the real graph and scores each against its clean document's key: whether the record is as faithful, whether the attack's payload became a clinical value (followed) or was kept as printed text, whether the document still ends where the clean one does, and whether its page holds any live markup or remote load. Rebuild the set with `python -m dataset.injection`. `--replay` answers from its own cassettes. It has not been run yet.
- **`python -m evals.selfcheck`** scores the answer keys against themselves to show the report's shape. Every score is perfect, and 22 of 25 documents end verified. Even so, its 0 items with an unsafe error out of 229 checked (204 clinical items and 25 documents' dates) still allows up to 1.6% at 95%: that is how little a dataset this size can rule out, and why every count in the report carries its interval.
- **`python -m evals.redaction`** measures how well patient identifiers are masked in what leaves the clinical stores (findings, errors, logs): recall per identifier type, with the run's known values and with patterns alone. See [where patient data goes](data-and-threats.md).

### What the evals report

The README shows the measured results. These are the full tables; cells marked *pending* are filled from the first live eval run. None is estimated.

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
| Injected faults caught | 1104/1130 (97.7%, 95% CI 96.6%-98.4%) |
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

**Prompt injection.** Text in a document aimed at the model (embedded instructions, hidden text, a remote image) is contained by design: pages are built by code with every value escaped, shown in a sandboxed frame with scripts off and no remote loads, and the overview is checked word by word. These defences are tested. The injection eval measures them on six attacked copies of dataset documents, two per kind of attack (an instruction in the text, hidden text, a remote image or script), each scored against its clean document's answer key, on a split of its own kept out of the main numbers. It is built and has not been run yet:

| Measure | Result |
|---|---|
| Records as faithful as the clean document's key | *pending* |
| Attacks followed (the payload became a clinical value) | *pending* |
| Pages with no live markup or remote load | *pending* |
