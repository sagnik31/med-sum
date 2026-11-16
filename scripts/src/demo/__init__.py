"""
A demo you can run without Ollama: two dataset documents through the real
document graph, answered from the recorded model replies (cassettes), in a
throwaway database, with the trace, the findings and the end state printed.
Run from scripts/src:

    python -m demo [--model M] [--docs d09,d15] [--cassettes DIR]

The defaults are d09 (a one-page lab report that ends verified) and d15 (a
prescription with no date, which a clinician must review). The cassettes
are the eval runner's (scripts/evals/cassettes/<model>/<doc>.json), written
by its first live run; until they exist the demo says which are missing and
how to record them, and starts nothing. Every step is the real code: the
pipeline, its validators and signals, its tracing into Postgres. Only the
model's replies come from the files.

The database is created and dropped on the server of MEDSUM_EVAL_ADMIN_URL
(with MEDSUM_ENV=dev, the local server as postgres) under the name
med_sum_demo, never med_sum. Everything is synthetic.
"""

import time
from collections.abc import Callable
from pathlib import Path

import psycopg

import llm
import settings
from evals import run as runner
from evals.cassettes import Cassette, CassetteMiss, ReplayClient
from evals.keys import answer_key, documents
from evals.score import score_document
from pipeline.signals import SIGNALS

DOCUMENTS = ("d09", "d15")
DB_NAME = "med_sum_demo"
SHOWN_SEVERITIES = ("error", "flag")  # info findings (unused lines) are left out

_STEPS = """
SELECT s.seq, s.node, s.page, s.attempt, s.status, s.model, s.model_role,
       s.prompt_tokens, s.completion_tokens, s.findings, s.error,
       EXTRACT(EPOCH FROM (s.ended_at - s.started_at)) * 1000 AS ms,
       s.parent_step_id IS NOT NULL AS child
FROM pipeline_steps s JOIN pipeline_runs r ON r.id = s.run_id
WHERE r.document_id = %s
ORDER BY r.started_at, s.seq
"""


def missing(model: str, doc_ids: list[str], cassettes: Path) -> list[Path]:
    """The cassettes the demo needs that aren't there."""
    paths = [runner.cassette_path(model, d, cassettes) for d in doc_ids]
    return [p for p in paths if not p.exists()]


def how_to_record(model: str, doc_ids: list[str], paths: list[Path]) -> str:
    listed = "\n".join(f"  {p}" for p in paths)
    return (
        "The demo answers from recorded model replies, and these are missing:\n"
        f"{listed}\n\n"
        "They are written by a live eval run, which calls Ollama (a few minutes\n"
        "per page on a CPU). To record just the demo's documents, from scripts/src:\n\n"
        f"  python -m evals.run --live --model {model} --docs {','.join(doc_ids)}"
        " --no-history\n\n"
        "The full live eval run records them too; commit the cassettes with its\n"
        "report so anyone can run the demo without Ollama.\n"
    )


def trace(document_id: str) -> list[dict]:
    """The run's steps for one document, in order."""
    with psycopg.connect(settings.database_url()) as conn, conn.cursor() as cur:
        cur.execute(_STEPS, (document_id,))
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _step_line(s: dict) -> str:
    where = s["node"]
    if s["page"]:
        where += f" p{s['page']}"
    if s["attempt"] > 1:
        where += f" (attempt {s['attempt']})"
    if s["child"]:
        where = "  " + where
    model = f"{s['model']} ({s['model_role']})" if s["model"] else "code"
    tokens = ""
    if s["prompt_tokens"] is not None or s["completion_tokens"] is not None:
        tokens = f"{s['prompt_tokens'] or 0}+{s['completion_tokens'] or 0} tok"
    shown = [f for f in s["findings"] if f["severity"] in SHOWN_SEVERITIES]
    notes = f"{len(shown)} finding(s)" if shown else ""
    if s["error"]:
        notes = f"error: {s['error']}"
    ms = f"{float(s['ms']):.0f} ms" if s["ms"] is not None else ""
    return (
        f"  {s['seq']:>3}  {where:<22} {s['status']:<6} {model:<26} "
        f"{ms:>9} {tokens:>14}  {notes}"
    ).rstrip()


def _finding_line(f: dict) -> str:
    where = f.get("item_id") or "document"
    if f.get("field"):
        where += f".{f['field']}"
    lines = ",".join(str(n) for n in f.get("lines") or [])
    cite = f" p{f['page']}:{lines}" if f.get("page") else ""
    return f"  {f['severity']:<5} {f['validator']:<20} {where}{cite}: {f['message']}"


def report_document(doc_id: str, final: dict | None, steps: list[dict]) -> list[str]:
    entry = documents()[doc_id]
    out = [
        f"== {doc_id}: {entry['type']}, {entry['pages']} page(s), "
        f"{entry['file'].split('/')[-1]}",
        "",
        "Trace (each step as stored in pipeline_steps):",
        *(_step_line(s) for s in steps),
        "",
    ]
    findings = [f for s in steps for f in s["findings"]
                if f["severity"] in SHOWN_SEVERITIES]  # fmt: skip
    out.append("Findings:" if findings else "Findings: none (errors and flags)")
    out += [_finding_line(f) for f in findings]
    if final is None:
        out += ["", "End state: the run failed", ""]
        return out
    hits = [h for h in final.get("signals", []) if h.routes]
    out.append("")
    out.append("Signals sending it to review:" if hits else "Signals: none that route")
    out += [f"  {h.signal}: {SIGNALS[h.signal].reason}"
            + (f" ({h.item_id}.{h.field})" if h.item_id and h.field else "")
            for h in hits]  # fmt: skip
    score = score_document(final["record"], answer_key(doc_id))
    f1 = score.all_fields.f1
    out += [
        "",
        f"End state: {final['end_state']} "
        f"(the dataset expects {entry['expected_end_state']})",
        f"Against the answer key: field F1 {f1:.3f}, "
        f"{len(score.unsafe)} unsafe error(s)",
        "",
    ]
    return out


def run(
    model: str | None = None,
    doc_ids: list[str] | None = None,
    cassettes: Path = runner.CASSETTE_DIR,
    *,
    database: Callable[[], object] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """
    The demo. 0 when every document ends as the dataset expects, 1 when one
    doesn't, 2 when a cassette is missing or no longer matches (nothing ran,
    or the run stopped).
    """
    model = model or llm.PRIMARY_MODEL
    doc_ids = list(doc_ids or DOCUMENTS)
    gone = missing(model, doc_ids, cassettes)
    if gone:
        out(how_to_record(model, doc_ids, gone))
        return 2
    database = database or (lambda: runner.EvalDatabase(name=DB_NAME))
    start = time.monotonic()
    finals: dict[str, dict | None] = {}
    lines: list[str] = []
    with database() as db:
        try:
            _, outcomes = runner.run_model(
                model,
                doc_ids,
                user_id=db.user_id,
                client_for=lambda d: ReplayClient(
                    Cassette.load(runner.cassette_path(model, d, cassettes))
                ),
                on_final=finals.__setitem__,
            )
        except CassetteMiss as e:
            out(f"A recorded reply no longer matches: {e}")
            return 2
        for doc_id in doc_ids:
            final = finals[doc_id]
            steps = trace(final["document_id"]) if final else []
            lines += report_document(doc_id, final, steps)
    expected = {d: documents()[d]["expected_end_state"] for d in doc_ids}
    as_expected = all(outcomes[d].end_state == expected[d] for d in doc_ids)
    out(f"med-sum demo: {len(doc_ids)} documents with {model} as the primary, "
        "model replies from cassettes, no Ollama\n")  # fmt: skip
    out("\n".join(lines))
    out(f"Done in {time.monotonic() - start:.1f} s; the throwaway database is dropped.")
    return 0 if as_expected else 1
