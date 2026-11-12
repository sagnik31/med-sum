"""
The eval runner: every dataset document through the real document graph,
scored against its answer key, for each model as the primary.

    python -m evals.run --live   [--model M ...] [--docs d01,d02] [--split dev]
    python -m evals.run --replay [--model M ...] [--docs ...] [--split ...]

--live calls Ollama and records every reply as a cassette
(scripts/evals/cassettes/<model>/<doc>.json); a live run of the whole
dataset takes hours on a CPU, so ask before starting one. --replay runs the
same graph from those cassettes without Ollama, in seconds, and fails with
"re-record" when a prompt, page, option or schema has changed since the
recording (evals/cassettes.py).

Each model runs as the primary with the other as its fallback, as the
service runs; a record the fallback produced is counted (OP-1) so the
report can tell. Every run uses its own fresh database
(MEDSUM_EVAL_DB_NAME, default med_sum_evals, created and dropped on the
server of MEDSUM_EVAL_ADMIN_URL), never med_sum, and validator 7's today
is fixed (faultcheck.TODAY) so live and replay agree.

The report goes to scripts/evals/results/<label>/ (gitignored); a live run
also adds one small entry to the committed results history
(scripts/evals/history, evals/history.py).
"""

import argparse
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg

import llm
import model_health
import reset_db
from dataset.build import DATASET_DIR
from evals import history
from evals.cassettes import Cassette, CassetteMiss, RecordingClient, ReplayClient
from evals.faultcheck import TODAY
from evals.keys import answer_key, documents, expected_patient, manifest
from evals.report import DocumentResult, ModelRun, build_report, write_report
from evals.score import score_document
from evals.transcription import score_transcription
from pipeline import checkpoints, graph, nodes
from pipeline.state import initial_state

RESULTS_DIR = DATASET_DIR.parent / "results"
CASSETTE_DIR = DATASET_DIR.parent / "cassettes"
ADMIN_URL = os.environ.get(
    "MEDSUM_EVAL_ADMIN_URL", "postgres://postgres:postgres@localhost:5432/postgres"
)
EVAL_DB_NAME = os.environ.get("MEDSUM_EVAL_DB_NAME", "med_sum_evals")
UPLOADED_BY = "Eval runner"


def cassette_path(model: str, doc_id: str, root: Path = CASSETTE_DIR) -> Path:
    slug = "".join(c if c.isalnum() or c in "-." else "_" for c in model)
    return root / slug / f"{doc_id}.json"


@dataclass
class Outcome:
    """What one document's run gave, beyond its scores."""

    end_state: str | None
    signals: list[str]  # routing signal ids
    record_model_role: str | None
    error: str | None = None


def run_one(doc_id: str, user_id: str, dataset_dir: Path = DATASET_DIR):
    """One document through the graph; the final state, or the error."""
    entry = documents(dataset_dir)[doc_id]
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (user_id, original_name, content_type,"
                " storage_path) VALUES (%s, %s, %s, %s) RETURNING id",
                (user_id, entry["file"].split("/")[-1], entry["content_type"],
                 str(dataset_dir / entry["file"])),
            )  # fmt: skip
            document_id = str(cur.fetchone()[0])
    state = initial_state(
        document_id,
        file_path=str(dataset_dir / entry["file"]),
        uploaded_by=UPLOADED_BY,
        expected_patient=expected_patient(doc_id, dataset_dir),
    )
    return graph.run_document(state)


def run_model(
    model: str,
    doc_ids: list[str],
    *,
    user_id: str,
    client_for: Callable[[str], object],
    dataset_dir: Path = DATASET_DIR,
    after_document: Callable[[str], None] = lambda doc_id: None,
) -> tuple[ModelRun, dict[str, Outcome]]:
    """
    Every document with `model` as the primary. `client_for(doc_id)` gives
    the model client for that document (recording or replay);
    `after_document(doc_id)` runs after each, e.g. to save its cassette. A
    CassetteMiss is raised at once: a replay that can't answer is not a
    result.
    """
    other = llm.FALLBACK_MODEL if model != llm.FALLBACK_MODEL else llm.PRIMARY_MODEL
    saved_models, saved_client, saved_today = (
        dict(llm._MODELS),
        llm._client,
        nodes.today,
    )
    llm._MODELS.update({"primary": model, "fallback": other})
    nodes.today = lambda: TODAY
    run, outcomes = ModelRun(model), {}
    try:
        for doc_id in doc_ids:
            key = answer_key(doc_id, dataset_dir)
            model_health.reset()
            llm._client = client_for(doc_id)
            llm._digests.clear()
            start = time.monotonic()
            try:
                final = run_one(doc_id, user_id, dataset_dir)
            except CassetteMiss:
                raise
            except Exception as e:  # the run failed: everything is missed
                seconds = time.monotonic() - start
                run.documents.append(
                    DocumentResult(
                        doc_id,
                        score_document(None, key),
                        score_transcription(None, key),
                        seconds,
                    )
                )
                outcomes[doc_id] = Outcome("failed", [], None, type(e).__name__)
                after_document(doc_id)
                continue
            seconds = time.monotonic() - start
            record = final["record"]
            run.documents.append(
                DocumentResult(
                    doc_id,
                    score_document(record, key),
                    score_transcription(record.pages or None, key),
                    seconds,
                )
            )
            routing = sorted({h.signal for h in final.get("signals", []) if h.routes})
            role = final.get("parse_model_role") if final.get("record_parsed") else None
            outcomes[doc_id] = Outcome(final["end_state"], routing, role)
            after_document(doc_id)
    finally:
        llm._MODELS.clear()
        llm._MODELS.update(saved_models)
        llm._client, nodes.today = saved_client, saved_today
        model_health.reset()
    return run, outcomes


# -------------------------------------------------------------------
# The eval database
# -------------------------------------------------------------------
def _url_with_database(url: str, name: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    return urlunsplit(urlsplit(url)._replace(path="/" + name))


class EvalDatabase:
    """A fresh database for one eval run, dropped afterwards."""

    def __init__(self, admin_url: str = ADMIN_URL, name: str = EVAL_DB_NAME):
        if name == "med_sum":
            raise ValueError("the eval runner never uses med_sum")
        self.admin_url, self.name = admin_url, name
        self.url = _url_with_database(admin_url, name)

    def _admin(self, sql: str) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(sql)

    def __enter__(self) -> "EvalDatabase":
        self._admin(f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")
        self._admin(f"CREATE DATABASE {self.name}")
        with psycopg.connect(self.url) as conn:
            reset_db.load_schema(conn)
        checkpoints.setup(self.url)
        self._old_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = self.url
        self.user_id = add_eval_user(self.url)
        return self

    def __exit__(self, *exc) -> None:
        if self._old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._old_url
        self._admin(f"DROP DATABASE IF EXISTS {self.name} WITH (FORCE)")


def add_eval_user(url: str) -> str:
    """The account the eval documents are uploaded by."""
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name) VALUES ('Eval Hospital') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        )
        (org,) = cur.fetchone()
        cur.execute(
            "INSERT INTO users (org_id, email, full_name) VALUES (%s, %s, %s) "
            "RETURNING id",
            (org, "evals@hospital.test", UPLOADED_BY),
        )
        return str(cur.fetchone()[0])


# -------------------------------------------------------------------
# Command line
# -------------------------------------------------------------------
def select(docs: str | None, split: str | None, dataset_dir: Path = DATASET_DIR):
    entries = documents(dataset_dir)
    ids = list(entries) if not docs else [d.strip() for d in docs.split(",")]
    unknown = [d for d in ids if d not in entries]
    if unknown:
        raise SystemExit(f"unknown documents: {', '.join(unknown)}")
    return [d for d in ids if split is None or entries[d]["split"] == split]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call Ollama, record")
    mode.add_argument("--replay", action="store_true", help="answer from cassettes")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--docs", help="comma-separated ids, e.g. d02,d09")
    parser.add_argument("--split", choices=["dev", "test"])
    parser.add_argument("--label")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cassettes", type=Path, default=CASSETTE_DIR)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args(argv)

    models = args.models or [llm.PRIMARY_MODEL, llm.FALLBACK_MODEL]
    doc_ids = select(args.docs, args.split)
    mode = "live" if args.live else "replay"
    label = args.label or f"{time.strftime('%Y-%m-%d')}-{mode}"
    runs, outcomes, digests = [], {}, {}
    live_client = llm._client
    with EvalDatabase() as db:
        for model in models:
            cassettes: dict[str, Cassette] = {}

            def client_for(doc_id: str, model=model, cassettes=cassettes):
                path = cassette_path(model, doc_id, args.cassettes)
                if args.live:
                    cassettes[doc_id] = Cassette()
                    return RecordingClient(live_client, cassettes[doc_id])
                if not path.exists():
                    raise CassetteMiss(f"{model}: no cassette {path}; re-record")
                return ReplayClient(Cassette.load(path))

            def save(doc_id: str, model=model, cassettes=cassettes):
                if args.live:
                    cassettes[doc_id].save(cassette_path(model, doc_id, args.cassettes))
                    digests.update(cassettes[doc_id].digests())

            try:
                run, out = run_model(
                    model,
                    doc_ids,
                    user_id=db.user_id,
                    client_for=client_for,
                    after_document=save,
                )
            except CassetteMiss as e:
                print(f"FAIL replay: {e}", file=sys.stderr)
                return 2
            runs.append(run)
            outcomes[model] = out
            print(f"{model}: {len(run.documents)} documents")

    report = build_report(label, runs, manifest(), outcomes=_as_dicts(outcomes))
    out_dir = args.out or RESULTS_DIR / label
    json_path, md_path = write_report(report, out_dir)
    print(f"wrote {json_path} and {md_path}")
    if args.live and not args.no_history:
        path = history.write_entry(history.entry(report, mode=mode, digests=digests))
        print(f"history: {path}")
    return 0


def _as_dicts(outcomes: dict[str, dict[str, Outcome]]) -> dict:
    return {
        model: {doc: o.__dict__ for doc, o in docs.items()}
        for model, docs in outcomes.items()
    }


if __name__ == "__main__":
    sys.exit(main())
