# med-sum: handover (minimal)

**Rebuilt 2026-11-22.** The full handover was lost on 2026-11-22: a helper
script truncated it while the machine was out of memory, and no copy could be
recovered (it is gitignored). At the owner's request this file keeps only what
the pre-eval implementation and the eval suite need. History before
2026-11-21 lives in git, the ADRs (`docs/adr/`) and the gap reports
(`.claude/gap-reviews/`).

**State as of 2026-11-22:** `main` at `403595b`, 23 commits ahead of origin,
not pushed. Tiers 0-3 are all done. Full suites run once on `403595b`, all
green: pytest 2495 passed (none skipped), go build/vet/test with the
database required, gofmt, npm lint/test (53)/build, ruff, faultcheck 97.7%.
Next: the smoke run, blocked on the owner's checks (sleep on AC is 15 min,
not off; CI can't run until a push; word-hunt status unknown).

**Update 2026-11-23:** `main` at `8afecf9`, pushed to origin (the suite's
reports and cassettes are public). The live eval suite has run (item 13), and its overviews are judged. Open:
the owner's answer on ADR 0013, and the MCP eval, which needs the app stack
set up first (item 13).

**Update 2026-11-23, later:** `main` at `ea449de`, pushed to origin. ADR 0013
was accepted and every part of it is built (item 14). Full suites run once
after the last part: pytest 2578 passed and 1 failed (a test that already
failed at HEAD, fixed in `ea449de`, its file then 17/17), go build/vet/test
with the database required, gofmt, npm lint/test (54)/build, ruff, faultcheck
1249/1275 (98.0%), gate pass. Open: the MCP eval (item 13), and the follow-ups
under item 14.

## 0. Start here

14. **2026-11-23: ADR 0013 built (the recommendations from the eval run and its judging).** One commit per part, on `main`, pushed. Each part's commit is in ADR 0013's status, and the `docs/` pages whose behaviour changed were updated with it.
    - **1. Facts wording, `e6b42a8`.** "Flagged in the latest report" is now "Latest flagged value", with its date: the judge found older values under the old heading. The overview prompt says the same. The overview cassettes miss on replay (the facts are in the request) and were not re-recorded.
    - **2. Overview must name allergies and the latest prescription, `50d5c19`.** The overview check also rejects an overview that leaves out a recorded allergy to a named substance or any medicine on the latest prescription ("No known allergies" needs nothing). A rejection takes the existing retry, then no overview. The service log and the overview eval record `missing_facts` (a count). The judged overviews: qwen's five still pass; gemma's fatima is now rejected, for the penicillin allergy only.
    - **3. No overview fallback, `1d66177`.** `llm.chat(..., fallback=False)`; the overview calls the primary only, so `gemma4:e2b` answers reading and parsing calls only.
    - **4. Checks for qwen's unsafe errors, `89a8f50`; repair cases rebuilt in `8beea24`.** Validators 11 (field placement: a strength left in a medicine's name, a frequency as the dose, a printed flag left in a value, an allergy's text without its own substance or reaction) and 12 (a result read from a table's header row), signals LC-23 and LC-24, five fault-check classes, all 121 injected faults caught. Of the 8 unsafe items in qwen's e2e auto-verified documents, 7 were of a kind these catch. The eighth, a medicine brand misread by one letter on an image page, no deterministic check can catch (no text layer, no list of medicine names); it is in `docs/limitations.md`. Not in the ADR: most "unsafe" items were a field in the wrong place, which the scorer's name pairing counts as one invented plus one missed item, so the gate overstates clinical risk. Not changed.
    - **5. No auto-verification until the safety gate passes, `18760ec`** (design proposed and approved before coding). No run ends `verified`: a document that passes every check ends `checks_passed`, waits in the review queue as "Checks passed, awaiting sign-off", and becomes `verified` only when a clinician signs it off (`POST /documents/{id}/sign-off`, permission `review.write`, audited as `document_signed_off`) or finishes its audit. An open audit sample can't be signed off without its audit. A rule in code, not a setting. Schema, Go API, AI service and frontend changed together. The evals count `checks_passed` where they counted `verified`, under the same report keys (`auto_verified`, `unsafe_in_verified`), so history and compare line up. ADR 0011 is marked amended by 0013.
    - **6. Judge severity, `a54ea83`.** The overview judge table counts omissions by their "[critical]"/"[major]"/"[minor]" prefix and unsupported sentences by their `severity` field (unrated is counted, never dropped). `2026-11-22-suite-overview` was regenerated from its verdicts, no model call: gemma 1 critical omission, qwen 3 minor.
    - **Test fix, `ea449de`.** `test_evals_repair.py` read the committed history, so its replay expected the real qwen digest and refused the fake model's; failing since the live repair results were committed. Its tests now get an empty history in a temporary folder, as the other eval tests do.
    - **Follow-ups, not done.** (1) The repair eval's cassettes miss on the new and moved cases of `8beea24`, and the overview cassettes on part 1's wording, until live runs re-record them (no live run without the owner's go-ahead). (2) Not covered by the ADR: a `needs_review` document still becomes `verified` once its flagged values are reviewed, though its unflagged values could be as wrong as a `checks_passed` one's. Needs the owner's decision. (3) The sign-off was tested in Go and the frontend's module tests, not clicked through in a browser against a running stack. (4) Found 2026-11-23 in a docs pass: `python -m evals.run --replay` (so CI's eval-replay job) now exits 2 on a `qwen3.5:4b` repair whose findings changed since the recording, most likely from validators 11 and 12 (`89a8f50`); a live e2e run re-records it. `python -m demo` still replays. `docs/limitations.md` says so.

13. **2026-11-22/23: the live eval suite (everything but mcp_eval).** All runs exited 0 and are committed, one commit per run. Progress, timings and notes are in `scripts/evals/reports/SUITE-PROGRESS.md`.
    - **Commits.** vision `0e12b0a` (~2.5 h), reasoning `8a9bbd4` (~10.5 h), e2e `3c0bd89` (~9 h 47 min; paused after qwen's half at the owner's request, resumed for gemma's), overview `4bfe38f` (~6 min, not judged yet), repair core `7e7c8af` (~2 h 16 min), injection `cf73479` (~2 h), toolprobe `53a7849` (~3 min).
    - **Headlines (qwen3.5:4b / gemma4:e2b).** Vision value recall 99.9% / 98.3%, CER 11.0% / 11.3% (both pass the 95% gate). Reasoning field F1 91.1% / 91.4%, unsafe 10/236 / 11/236. E2e field F1 91.4% / 89.3%, unsafe 10/236 / 26/245, unsafe in auto-verified documents 8/149 / 9/78. **No model chosen**: both fail the 1% unsafe-error gate. Overview shown 5/6 / 1/6. Repair fixed 14/15 / 14/15, tools 15/15 / 13/15; the report supports fixed for both; gemma made no tool calls in the tools variant. Injection faithful 5/6 / 2/6, followed the attack 0/6 / 1/6, unsafe 1 / 6. Toolprobe 6/7 / 7/7 (qwen called a tool when none was needed).
    - **Report oddities seen, not fixed (no code changes during the suite).** (1) The vision report shows 0 in every document-count column, although all 25 documents were scored. (2) Reasoning and e2e reports count unsafe items over 204 clinical items in the model choice and over 236/245 items in the overview table. (3) History entries say `dirty: true` while the resume fingerprint saw no change outside the eval outputs. (4) The e2e stage was not cheap: it called the models on most documents (~8 min each in real time), even ones read exactly in the vision run. (5) Repair and injection write no history entry; toolprobe writes only to the gitignored `results/` (copied into `reports/2026-11-22-toolprobe/`). (6) Progress lines are block-buffered when stdout goes to a file; run with `PYTHONUNBUFFERED=1`.
    - **Done 2026-11-23: the overview judge.** `claude-opus-5-5` judged the six shown overviews with `rubric.md` (severities and gates fixed first, thresholds approved by the owner) and wrote `verdicts.json`; `--verdicts` merged it into the overview report. Results and recommendations: `docs/eval-results.md`, `docs/eval-judge.md`, `docs/model-recommendation.md`, and ADR 0013 (accepted by the owner 2026-11-23; it answers E-c and the default repair variant; its unbuilt parts are listed in its status).
    - **Open: the MCP eval (`evals.mcp_eval`), skipped at the owner's request.** It asks a local model questions built from the answer keys (a value in a named document; a test no document holds) and lets it read the records only through the MCP server, so through the Go API, its organisation scope and its audit log. A value answer passes when cited, grounded in lines a tool returned, numbers-checked, located at the key's page and lines, and correct; an absent one when it says "The records hold no answer." with no number. Default: test split, primary model only (`--model-role fallback` for gemma, a second run). `results.json` goes to the gitignored `results/<date>-mcp/` by design (it quotes synthetic values).
      - **Owner's setup (secrets and passwords, so the owner does these).** (1) Copy `backend/.env.example` to `backend/.env.local` and `scripts/.env.example` to `scripts/.env.local` (neither existed on 2026-11-23); the same `INTERNAL_API_TOKEN` (32+ chars) in both; `DATABASE_URL`, `MEDSUM_ENV=dev` and `MEDSUM_DEV_PASSWORD` per `docs/setup.md`. (2) `go run ./cmd/medsumctl seed-dev` in `backend/` (the dev clinician that `dataset.load` logs in as). (3) `go run ./cmd/medsumctl create-user --email mcp-client@med-sum.test --name "MCP client"` and set its password with the printed link. (4) Copy `scripts/mcp.env.example` to `scripts/mcp.env.local` and fill in `MEDSUM_MCP_EMAIL` and `MEDSUM_MCP_PASSWORD`. (5) Start the API (`backend/run.ps1`) and the AI service (`scripts/run.ps1`).
      - **Then the agent.** From `scripts/src` with `MEDSUM_ENV=dev`: `python -m dataset.load --yes` (writes the synthetic patients and documents to the dev database and starts processing; live model runs, hours on the CPU), wait until every document has finished processing, then `python -m evals.mcp_eval --live --label suite-mcp` in the background (~3.5-4 h estimated), and commit what it produces outside `results/`. No commits while it runs (the resume fingerprint includes HEAD). No `ollama pull`.

12. **2026-11-21: the pre-eval implementation plan (owner: "finish all the implementation tasks first"; decisions no. 103-107).** Every change that doesn't depend on how the eval run goes is built **before** the live eval suite, so that suite runs once on final code. A live run costs hours on the CPU, so the order below puts anything that changes a model request (and so every cassette key) first. Code-only changes (validators, routing, scoring) could be re-scored later with `--replay`, but they land first anyway so the first committed report and history entry reflect them. One commit per item, with tests that fail on the old code, the fault check kept at or above its baseline, and the matching ADR or `docs/` page updated. Push only when the owner asks. **Not in this plan, because they depend on the run:** E-a..E-e (gates, which model is primary, MJ promotion, tool-probe commit), agentic G22's deadline per model call (needs live timings), the default repair variant (from the repair eval's verdict), and E5's bad-prompt demonstration.
    - **Tier 0: before any live eval call (they change requests, or a crash would lose hours).**
      - **0.1 Seed (decision no. 104).** **Done `ba672f6`.** `seed=0` in `llm.GENERATION_OPTIONS`; stored with the run and in each cassette entry; ADR 0005 note.
      - **0.2 Agentic G20 (decision no. 103).** **Done `5588879`.** `test_name` and the document dates join `score.UNSAFE_FIELDS`. The report's interval counts items with an unsafe error, so it can't exceed the items and crash `wilson`. The MCP eval does a whole-word value match against the cited lines only, with a narrow citation. `compare` refuses runs with a different dataset, documents, split or model set.
      - **0.3 Eval runs checkpointed and resumable (decision no. 107).** **Done `b3d3fac`.** See the design below. Applies to `evals.run`, `evals.injection` and `evals.repair`, and to `evals.mcp_eval` per question. `evals.toolprobe` is about 2 minutes and is left as it is.
      - **0.4 Replay checks the model build.** **Done `b185499`.** `ReplayClient(expect_digests=...)` exists but is never used. Replay (and CI's eval-replay job) passes the digests from the cassettes' history entry and fails when the local build differs (ADR 0005/0009 challenge).
      - **0.5 Stop marker printed on a page.** **Done `d3fb166`.** Today the page is cut there silently (ADR 0005 challenge). Raise a document-level signal when the reply had text after the marker, or when the page text contains the marker. The injection eval may trigger it.
    - **Tier 1: code only, but they change the eval's numbers.**
      - **1.1 Agentic G11 (decision no. 105).** **Done `d3179ed`.** A routing finding when the expected patient is known but nothing printed (name, ID, DOB) was read; a name of initials only never matches. Add a fault-check class.
      - **1.2 ADR 0002 / LC-15 (decision no. 106).** **Done `f9dc449`.** Code unchanged; ADR 0002 amended to say the model's `other` against a keyword-chosen type is a disagreement.
      - **1.3 Agentic G16.** **Done `c2b451a`.** Default (owner didn't object, 2026-11-21): code follows ADR 0003. Allow `original=None` corrections for the three date fields and re-derive the document date; update the test at `tests/test_review.py:257`.
      - **1.4 Agentic G10.** **Done `aa9623a (format fix 4acb34a)`.** Default: refuse to start unless the resolved `OLLAMA_HOST` is loopback, and allow-list the two configured models (case-insensitive); ADR 0005 defines "cloud" by host.
    - **Tier 2: correctness outside the evals.** Each item takes the default named here unless the owner says otherwise.
      - **2.1 ADR 0011.** **Done `3e642be`.** LC-13 (another patient) and LC-17 (rows missing) aren't cleared by a plain document confirm; each needs its own acknowledgement (Go, Python, React together).
      - **2.2 ADR 0006.** **Done `c9470d8`.** The summary claim gets a heartbeat like jobs, so a long build isn't taken over at 30 minutes.
      - **2.3 Frontend G5.** **Done `b8dc055`.** Background polls send `X-Medsum-Background: 1` (CORS allow-list); the middleware checks the session but doesn't touch `last_seen_at`; backoff, and polling keeps going through a transient error. One commit, Go and React together, with a Go test.
      - **2.4 Backend G8.** **Done `ba8466a`.** The review-queue audit row records the patient ids it listed.
      - **2.5 Backend G4.** **Done `c4a434a`.** A separate, longer, configured timeout for `/internal/review-apply`.
      - **2.6 ADR 0012.** **Done `0cbac92`.** The MCP dev-only gate asks the API for its environment instead of trusting the server's own env file.
    - **Tier 3: hardening, done in this plan too.**
      - **3.1 Backend G5.** **Done `56815b3`.** Graceful shutdown (`signal.NotifyContext` + `srv.Shutdown`), plus a `medsumctl` check for upload files that no row names.
      - **3.2 Frontend G9.** **Done `c4dad26`.** A strict script CSP on `vite preview`, and a line in `docs/limitations.md`.
      - **3.3 Frontend G6.** **Done `6d9a656`.** Audit mode marks each value as reviewed or not and shows "N left"; it turns on from the document's own state (small Go change); the banner is reworded.
      - **3.4 react-router 7.** **Done `0f99e4d`** (7.18.6; 8 needs React 19). Migrate (two moderate advisories on 6.30.6 that can't be reached today).
      - **3.5 `HtmlFrame` meta refresh.** **Done `0746ed7`.** A Python test that no `<meta` reaches a built page.
      - **3.6 Unconfirmed backend items.** **Done: rate-limit key `133cafe` (TRUSTED_PROXIES + Vite xfwd); sandbox `403595b` (checked in the pane's Chromium PDF viewer, framed; top-level not checked).** CSP `sandbox` on the served upload (needs a browser check of Chrome's PDF viewer) and the login rate-limit key behind the Vite proxy. Check them, then fix or drop them.
    - **Design of 0.3, resumable eval runs (decision no. 107).** The unit of progress is one (model, document) for `run` and `injection`, one (model, variant, case) for `repair`, and one question for `mcp_eval`.
      - **Run directory.** A run is named on its first start (`--label`, or date plus mode) and gets a working directory `scripts/evals/results/<name>/progress/` (gitignored). It holds `run.json`, the fingerprint: code commit and dirty flag, prompt hashes, model digests, generation options (seed included), dataset manifest hash, models, documents and split. It also holds one file per finished unit with its `DocumentResult`, `Outcome` and anything `on_final` keeps. Each file is written atomically (temp file, then `os.replace`), right after the unit's cassette is saved.
      - **Resuming.** `--resume <name>` loads `run.json` and refuses with a message naming what differs if the fingerprint doesn't match the current code, prompts, digests or options: a resumed run must be one run. It then skips finished units, runs the rest in the original order, and builds the report and the history entry from all of the units, exactly as an uninterrupted run would. Without `--resume`, an existing progress directory for that name is refused rather than overwritten.
      - **What a crash loses.** Only the unit in flight (at most one document, about 7-12 minutes on qwen). Its half-filled cassette is never saved, and the eval database is fresh on each start, which is safe because each unit inserts its own document.
      - **Infrastructure errors.** A model outage (`llm.ModelUnavailableError` when both models are down, or Ollama unreachable), a lost database connection or a full disk **stop the run** instead of being scored as a failed document. Today `run_model` scores any `Exception` as a failed run, so an Ollama crash at 3 a.m. would quietly fill the rest of the report with misses. Only an error from the document itself counts as a result.
      - **Progress.** One line per unit (unit, seconds, end state, units left, an estimate of the time left), so a long run can be watched.
      - **Tests.** With the fake model: kill the run after k units, resume, and the report is identical to an uninterrupted run's; a changed prompt or option refuses the resume; an outage stops the run and writes no unit.
    - **Manual checks for the owner before the live run.** (1) The CI run of the 2026-11-20 push (`46a808e..b2452ca`) is green (`gh` isn't installed in the agent's shell). (2) When word-hunt has finished, restart Ollama so its 262k-context qwen load is gone (med-sum uses `MEDSUM_NUM_CTX` 16384), and check that `ollama ps` shows nothing loaded. (3) During the run: plugged in, sleep and Windows Update paused, and no `ollama pull` (a new digest breaks replay). (4) Postgres reachable on `MEDSUM_EVAL_ADMIN_URL`.
    - **Then, before the full suite: smoke run.** Done 2026-11-22: live on d09 (qwen 501 s, verified; gemma 928 s, needs_review, its parse fell back to qwen), kill and resume, and replay all worked; the replay's report matched the live one.
    - **Eval stages (2026-11-22, `5c0b8f2`, `02b02c3`, `d2004c3`).** `evals.run --stage vision|reasoning|e2e`; e2e reuses both stages' cassettes and calls models only where a reading differs. `evals.overview` with a judge (a separate session judges `judge.json` per `judge.md`; `--verdicts` merges). Suite order: vision (~2.5-3 h), reasoning (~2-8 h), e2e (cheap), overview (~0.5-1 h), repair core (2-3 h), injection (1-2 h), toolprobe (2 min), mcp_eval (~3.5-4 h, needs the dataset loaded). Estimates come from d09 alone.

5. When the review happens, record each answer: add it to section no. 7 as a decision, update section no. 8 or 9 if it changes either, and remove the question from section no. 11. Add any new question that comes up while reading the code to section no. 11, with the step it gates.

## 5. Commits since `b2452ca`

| Commit | Change |
|---|---|
| `ba672f6` | feat(scripts): send seed=0 on every model call and keep it with the run |
| `5588879` | fix(scripts): close the eval scoring holes (test names, dates, MCP match, compare) |
| `b3d3fac` | feat(scripts): checkpointed, resumable eval runs that stop on an outage |
| `b185499` | feat(scripts): a replay checks the model build its cassettes were recorded with |
| `d3fb166` | feat(scripts): LC-21 when a page prints the end-of-page marker |
| `d3179ed` | fix(scripts): a document whose patient can't be confirmed goes to review (LC-22) |
| `f9dc449` | docs(adr): ADR 0002 says the model's `other` against a keyword type is LC-15 |
| `c2b451a` | fix(scripts): an undated document can be dated in review |
| `aa9623a` | fix(scripts): the cloud guard decides by host and allow-lists the two models |
| `4acb34a` | style(scripts): ruff format the cloud-guard tests |
| `3e642be` | feat: LC-13 and LC-17 must be acknowledged by name in review |
| `b8dc055` | fix: background polls don't keep a session alive, and back off |
| `c9470d8` | fix(scripts): the patient summary's claim has a heartbeat |
| `ba8466a` | fix(backend): the review-queue audit row names the patients it listed |
| `c4a434a` | fix(backend): review-apply gets its own, longer, configured timeout |
| `0cbac92` | feat: the MCP server serves only when the API itself runs in dev |
| `56815b3` | fix(backend): graceful shutdown, and a check for upload files no row names |
| `c4dad26` | fix(frontend): a strict content security policy on the built app |
| `6d9a656` | feat: audit mode comes from the document's open audit, value by value |
| `0f99e4d` | chore(frontend): react-router 7 |
| `0746ed7` | test(scripts): no <meta> from a document reaches a built page |
| `133cafe` | fix(backend): the login limit keys on the browser behind a trusted proxy |
| `403595b` | fix(backend): the served upload's CSP sets sandbox |
| `f77b571` | docs(adr): accept ADR 0013, the model choice after the first live evals |
| `e6b42a8` | fix(scripts): give flagged facts as the latest flagged value with its date |
| `50d5c19` | feat(scripts): reject an overview that leaves out an allergy or the latest prescription |
| `1d66177` | feat(scripts): no fallback model for the patient overview |
| `89a8f50` | feat(scripts): checks for the misplaced parts qwen3.5:4b's unsafe errors came from |
| `8beea24` | fix(evals): rebuild the repair cases for the misplaced-part faults |
| `18760ec` | feat: no auto-verification until the safety gate passes |
| `a54ea83` | feat(evals): count the overview judge's omissions and errors by severity |
| `ea449de` | test(evals): give the repair eval tests their own history |

## 7. Decisions behind the plan

| # | Decision (date, who) | Detail |
|---|---|---|
| 103 | Unsafe errors include test names and dates (2026-11-21, owner, agentic G20) | A wrong observation `test_name` and wrong document dates count as unsafe errors in `evals/score.py` (the model-choice gate), as the README already says. The rest of G20 (report crash on unsafe > items, whole-token MCP match with a narrow citation, `compare` refusing unlike runs) is fixed with it. Before the live run. |
| 104 | A fixed seed on every model call (2026-11-21, owner, ADR 0005 challenge) | Every request sends `seed=0` with temperature 0; the seed is stored with the run and in each cassette entry. Lands before the live run, since it changes every cassette key. ADR 0005 updated. |
| 105 | A patient must be confirmed to verify (2026-11-21, owner, agentic G11) | When the expected patient is known but no printed name, ID or DOB was read, a **routing** finding sends the document to review; a name of initials only never matches. Code-only (replayable), landed before the live run so the first report reflects it. |
| 106 | LC-15 on the model's `other` stays (2026-11-21, owner, ADR 0002 challenge) | Code kept: the model saying `other` while keywords pick a type is a disagreement and raises LC-15. ADR 0002 amended to say so. Nothing relaxed. |
| 107 | Eval runs are checkpointed and resumable (2026-11-21, owner) | Every live eval suite saves progress after each unit (document, repair case, MCP question) and resumes with `--resume <name>` from the last finished unit, refusing if code, prompts, digests or options changed. Infrastructure errors (both models down, Ollama or Postgres unreachable) stop the run instead of being scored as failed documents. Design in section no. 0, item 12. Before the live run. |
| 108 | Full suites once, at the end (2026-11-22, owner) | Per commit, run the item's own tests plus ruff/lint and the fault check; run the full pytest, Go and npm suites once after every item is implemented, and fix any regression in its own commit. |

## 11. Open questions for the owner

- **2026-11-23: does a reviewed `needs_review` document still need a
  sign-off?** Today it becomes `verified` once every flagged value is
  confirmed or corrected; ADR 0013 only stops runs that pass every check from
  ending `verified`. See item 14's follow-ups.
- **E-c and the default repair variant are answered** by ADR 0013
  (`qwen3.5:4b` stays primary; the fixed re-parse stays the default repair).

- **E-a to E-e wait for the live run** and are decided from its results: E-a
  rewording the safety and F1 gates, E-b `compare` tolerances (`MAY_FALL`),
  E-c which model is primary, E-d promoting an MJ signal, E-e the tool-probe
  commit. Also from the run: agentic G22's deadline per model call (needs live
  timings), the default repair variant (from the repair eval's verdict) and
  E5's bad-prompt demonstration.
