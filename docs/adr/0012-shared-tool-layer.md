# 0012. One read-only tool layer, used in-process and served over MCP through the API

- **Status:** Accepted, 2026-11-15. Built; the client eval (`evals/mcp_eval.py`) is built but not run yet.

## Context

Two things need to read a processed document with typed, read-only tools. The repair step (`pipeline/tool_repair.py`) lets a local model look up page lines, search the transcription and read the PDF's text layer before it corrects a parse. An external MCP client, such as a desktop assistant or an IDE, could let a hospital's staff ask about a patient's documents in their own tool. If each had its own tools, the two would drift: a citation format, a line limit or a fix to one would not reach the other, and what the repair step was measured on would not be what a client sees.

An MCP server is also an easy way to leak patient data. A server that connects to Postgres sees every organisation and bypasses the permissions in code, the organisation scope in SQL and the audit log (ADR 0010). And an MCP client's model often runs at a cloud provider, which patient data must never reach (ADR 0005, ADR 0008).

## Decision

**The tools are defined once, in `scripts/src/tools/`.** Each one has a name, a description written for a model, a Pydantic input model (whose JSON schema the model is shown), an output model, and a function of a `ToolSource` (one document's pages and its record). `get_lines`, `search_transcription`, `get_text_layer`, `list_items` and `get_item` are the shared set. The repair step calls them in-process on the run's own pages. The MCP server (`scripts/src/medsum_mcp/`) serves the same definitions, adds `document_id` to each input, and adds `find_patients` and `list_documents` to find a document. A change to a tool reaches both.

**The server reads through the Go API, never the database.** It logs in with a password as its own clinician account (email and password in the untracked `scripts/mcp.env.local`), keeps the session token, and logs in again when the API answers 401 (an idle or absolute timeout, a revoke, a password change). So the account's organisation scope, its permissions and the audit log apply as they do to a clinician in the browser. Another organisation's patient or document answers 404, as it does in the app. No new token type was added.

**Each tool call is one API request and one audit row naming the tool.** A document tool fetches the document's record (`GET /documents/{id}/record`) and runs the shared tool on it. The other two list patients or a patient's documents. Every request carries `X-Medsum-Tool: <name>`, and the API adds `tool` to the details of the audit row that read writes. The header is a label, not a credential: the actor is still the session's account, the API keeps only the name of a tool it knows and stores any other as `unknown` (2026-11-24; it kept any plain name before), and browsers can't send it, since it is not on the CORS allow-list. A call whose arguments don't fit the schema is answered before any request, so it reads nothing and leaves no audit row. Its error kind is logged locally. There is no cache, so a repeated call is audited again.

**Every answer carries its citations, and no value is bare.** An answer is `{"citations": [...], "result": {...}}`. Every entry of the result (a patient, a document, an item, a search hit, a range of lines) has its own `cite`, and `citations` lists them all: `patient:<id>`, `document:<id>`, `document:<id>#p2:7-9` for transcription lines, and `document:<id>#text-layer:p3:1-40` for the PDF's own text. An answer with no entries has no citations. The document tools read the record as clinicians reviewed it, with every correction applied, as the patient summary shows it (ADR 0011).

**The server serves synthetic data only.** It starts only under `MEDSUM_ENV=dev`, where every document is synthetic, and its own setting isn't enough: an env file can say dev while pointing at an API that holds real patients, so before serving it asks the API which environment it runs in (`GET /environment`) and serves only when the API says `dev` too, and the [MCP server docs](../api.md#mcp-server) say so: whatever a tool returns goes to the client's model, and that model may run at a cloud provider. Its log lines go to stderr and hold the tool, the outcome, the status and the time, never an argument or a value (ADR 0008).

**It is measured by an eval of a client using it** (`python -m evals.mcp_eval`). Questions about the synthetic patients go to a local model, which can call the tools only over the MCP server, so through the API and its audit. Each answer is scored for whether it cites, whether every citation is one the tools returned, whether its numbers appear in the lines it cites (the patient overview's number check, reused), and whether the model abstains when the records hold no answer.

## Alternatives rejected

- **A read-only MCP server over Postgres.** It would be quicker to build. But it sees every organisation, skips the permissions and the audit log, and needs a database role of its own with grants to keep in step with the API's.
- **Separate tools for the repair step and the server.** The format of citations and the limits on lines would drift, and the repair measurements would not describe what clients get.
- **A dev token or a service account type for the server.** The owner chose a password login as an ordinary clinician account. It needs nothing new in the API's auth, and an admin can disable or reset the account like any other.
- **One audit row per MCP session rather than per call.** A session can read many documents. An audit row per read is what the API already writes, and the tool label makes the rows of a client's session readable.
- **Bare values with a citation field beside them.** A client model drops what it is not made to keep. Citations at the top level and on every entry make "quote the citation" checkable.

## Consequences

- One tool definition serves two consumers, and its tests cover both. The repair step's limits (40 lines a call, 12 hits) bind the client too.
- Each document tool fetches the whole record, pages included, for every call. That is cheap for a local API and a few pages. A long document read many times would want a lighter route.
- A client's session leaves an audit trail that an admin can filter by actor (the server's account), with the tool on each row.
- The server can only read what its account's role can. Its account should be a clinician's, so `review.write` is there, but no tool writes.
- The server can't tell real data from synthetic. The dev-only start, checked against the API's own environment, and the documented rule are what keep real data from a cloud client.

## What would change it

- A tool that writes (confirming a value, say). It would need its own permission check in the API and a confirmation in the client, not a new tool beside these.
- Real patient data served to a client. That would need a local client model, and the start check changed to say so, never removed.
- Measured client results showing citations are ignored or invented. Then the answer format, or the tools' descriptions, change before anything else.
