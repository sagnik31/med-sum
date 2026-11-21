"""
The eval runner: every dataset document through the real document graph,
scored against its answer key, for each model as the primary.

    python -m evals.run --live   [--stage S] [--model M ...] [--docs d01,d02]
                                 [--split dev]
    python -m evals.run --replay [same options]

--stage picks what is measured (docs/adr/0009-measured-evals.md):

- vision: only reading the pages (open, then transcribe), scored against
  the dataset's expected transcriptions. No record is made.
- reasoning: the rest of the graph (classify, parse, repair, validate,
  render, finish) from the expected transcriptions, so a parse error is
  never a reading error. Scored on the record and its routing.
- e2e (the default): the whole graph. A live e2e run answers every
  request the vision and reasoning runs recorded (the same model, prompt,
  input and options): each page's reading, and, for a document the model
  read exactly as expected, its classify and parse, which are then the
  reasoning run's requests. So the model is called only where the e2e
  run differs from the stages; the recorded model time of the reused
  replies is added to the document's time.

The vision and reasoning cassettes sit in vision/ and reasoning/ under
the cassette directory; the e2e ones at its top.

--live calls Ollama and records every reply as a cassette
(scripts/evals/cassettes/<model>/<doc>.json); a live run of the whole
dataset takes hours on a CPU, so ask before starting one. --replay runs the
same graph from those cassettes without Ollama, in seconds, and fails with
"re-record" when a prompt, page, option or schema has changed since the
recording (evals/cassettes.py), or when a cassette was recorded with
another model build than the latest live run's history entry names or
than the one installed (replay_digests).

Each model runs as the primary with the other as its fallback, as the
service runs; a record the fallback produced is counted (OP-1) so the
report can tell. Every run uses its own fresh database
(MEDSUM_EVAL_DB_NAME, default med_sum_evals, created and dropped on the
server of MEDSUM_EVAL_ADMIN_URL), never med_sum, and validator 7's today
is fixed (faultcheck.TODAY) so live and replay agree.

A run saves its progress after each document and can be resumed
(`--resume <label>`) from the last finished one, refusing if the code,
prompts, model digests or options changed; an outage of every model, a lost
database or a full disk stops it instead of being scored (evals/progress.py).
Progress lines show each document's time and end state, and the time left.

A live run is kept: its full report (report.json and report.md, every
document's scores and field errors) goes to the committed
scripts/evals/reports/<name>/, and one small entry with its counts to the
committed results history (scripts/evals/history/<name>.json,
evals/history.py), both under the same name. A replay, or a live run with
--no-history, only reproduces or tries something, so its report goes to
scripts/evals/results/<label>/ (gitignored). --out overrides either place.
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
import settings
from dataset.build import DATASET_DIR
from evals import history, progress
from evals.cassettes import (
    Cassette,
    CassetteMiss,
    RecordingClient,
    ReplayClient,
    ReusingClient,
)
from evals.faultcheck import TODAY
from evals.keys import (
    answer_key,
    documents,
    expected_patient,
    manifest,
    with_text_layers,
)
from evals.report import DocumentResult, ModelRun, build_report, write_report
from evals.score import score_document
from evals.transcription import score_transcription
from pipeline import checkpoints, graph, nodes
from pipeline.state import initial_state
from record.document import DocumentRecord, PageTranscription

RESULTS_DIR = DATASET_DIR.parent / "results"
CASSETTE_DIR = DATASET_DIR.parent / "cassettes"
EVAL_DB_NAME = os.environ.get("MEDSUM_EVAL_DB_NAME", "med_sum_evals")
UPLOADED_BY = "Eval runner"
STAGES = ("e2e", "vision", "reasoning")
# The graph each stage runs, and the version its pipeline runs carry.
_GRAPHS = {
    "e2e": ({}, graph.GRAPH_VERSION),
    "vision": ({"only_transcribe": True}, graph.GRAPH_VERSION + "+vision-only"),
    "reasoning": (
        {"from_transcriptions": True},
        graph.GRAPH_VERSION + "+from-transcriptions",
    ),
}


def cassette_path(
    model: str, doc_id: str, root: Path = CASSETTE_DIR, stage: str = "e2e"
) -> Path:
    slug = "".join(c if c.isalnum() or c in "-." else "_" for c in model)
    base = root if stage == "e2e" else root / stage
    return base / slug / f"{doc_id}.json"


@dataclass
class Outcome:
    """What one document's run gave, beyond its scores."""

    end_state: str | None
    signals: list[str]  # routing signal ids
    record_model_role: str | None
    error: str | None = None


# A dataset document's row, uploaded by the eval account to its patient's
# record (made on first use, in the account's organisation, from the
# manifest: the same details the patient-match check is given).
_INSERT_DOCUMENT = """
WITH u AS (SELECT org_id FROM staff_users WHERE id = %(user)s),
p AS (
    INSERT INTO patients (org_id, mrn, full_name, date_of_birth, sex, created_by)
    SELECT org_id, %(mrn)s, %(name)s, %(born)s, %(sex)s, %(user)s FROM u
    ON CONFLICT (org_id, mrn) DO UPDATE SET full_name = EXCLUDED.full_name
    RETURNING id)
INSERT INTO documents (patient_id, uploaded_by, original_name, content_type, storage_path)
SELECT p.id, %(user)s, %(file)s, %(type)s, %(path)s FROM p
RETURNING id
"""


def insert_document(user_id: str, doc_id: str, dataset_dir: Path = DATASET_DIR) -> str:
    """A documents row for a dataset document, uploaded by the account; its id."""
    entry = documents(dataset_dir)[doc_id]
    patient = expected_patient(doc_id, dataset_dir)
    with psycopg.connect(settings.database_url(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                _INSERT_DOCUMENT,
                {"user": user_id, "mrn": patient.mrn, "name": patient.name,
                 "born": patient.date_of_birth, "sex": patient.sex,
                 "file": entry["file"].split("/")[-1], "type": entry["content_type"],
                 "path": str(dataset_dir / entry["file"])},
            )  # fmt: skip
            return str(cur.fetchone()[0])


def run_one(
    doc_id: str, user_id: str, dataset_dir: Path = DATASET_DIR, stage: str = "e2e"
):
    """One document through the stage's graph; the final state, or the error."""
    entry = documents(dataset_dir)[doc_id]
    document_id = insert_document(user_id, doc_id, dataset_dir)
    state = initial_state(
        document_id,
        file_path=str(dataset_dir / entry["file"]),
        uploaded_by=UPLOADED_BY,
        expected_patient=expected_patient(doc_id, dataset_dir),
    )
    if stage == "reasoning":
        key = answer_key(doc_id, dataset_dir)
        state["transcriptions"] = with_text_layers(doc_id, key.pages, dataset_dir)
    options, version = _GRAPHS[stage]
    return graph.run_document(
        state,
        graph=graph.build(**options),
        graph_version=version,
        # A run that stops after reading has no record to verify.
        end_state="needs_review" if stage == "vision" else None,
    )


def earlier_cassette(model: str, doc_id: str, root: Path) -> Cassette | None:
    """
    What the vision and reasoning runs recorded for this model and
    document, as one cassette; None when neither did. A live e2e run
    answers from it every request it holds: each page's reading, and, when
    the model read the pages exactly as the expected transcription, the
    classify and parse requests too, which are then the reasoning run's.
    """
    paths = [cassette_path(model, doc_id, root, s) for s in ("vision", "reasoning")]
    entries = [e for p in paths if p.exists() for e in Cassette.load(p).entries]
    return Cassette(entries) if entries else None


def scores_of(
    doc_id: str,
    record: DocumentRecord | None,
    pages: list[PageTranscription] | None,
    stage: str,
    dataset_dir: Path = DATASET_DIR,
):
    """
    A document's record and transcription scores for its stage: a vision
    run makes no record to score, and a reasoning run reads no page.
    """
    key = answer_key(doc_id, dataset_dir)
    rec = None if stage == "vision" else score_document(record, key)
    trans = None if stage == "reasoning" else score_transcription(pages, key)
    return rec, trans


def pages_of(final: dict | None) -> list[PageTranscription] | None:
    """The pages a run read: its record's, or (with no record) its state's."""
    if not final:
        return None
    record = final.get("record")
    if record is not None:
        return record.pages or None
    return list(final.get("transcriptions") or []) or None


def unit_data(final: dict | None, outcome: Outcome, seconds: float, **extra) -> dict:
    """What a finished document keeps in its progress file: the record and,
    for a run that made none, the pages it read (the scores are worked out
    from them again), its outcome and its time."""
    record = final.get("record") if final else None
    pages = None if record is not None else pages_of(final)
    return {
        "record": record.model_dump(mode="json") if record is not None else None,
        "pages": [p.model_dump(mode="json") for p in pages] if pages else None,
        "outcome": dict(outcome.__dict__),
        "seconds": seconds,
        **extra,
    }


def result_of(
    doc_id: str, data: dict, dataset_dir: Path = DATASET_DIR, stage: str = "e2e"
) -> tuple[DocumentResult, Outcome]:
    """A document's result and outcome from its progress file."""
    record = DocumentRecord.model_validate(data["record"]) if data["record"] else None
    if record is not None:
        pages = record.pages or None
    elif data.get("pages"):
        pages = [PageTranscription.model_validate(p) for p in data["pages"]]
    else:
        pages = None
    rec, trans = scores_of(doc_id, record, pages, stage, dataset_dir)
    return DocumentResult(doc_id, rec, trans, data["seconds"]), Outcome(
        **data["outcome"]
    )


def run_model(
    model: str,
    doc_ids: list[str],
    *,
    user_id: str,
    client_for: Callable[[str], object],
    dataset_dir: Path = DATASET_DIR,
    after_document: Callable[[str], None] = lambda doc_id: None,
    on_final: Callable[[str, dict | None], None] = lambda doc_id, final: None,
    on_unit: Callable[[str, dict | None, Outcome, float], None] | None = None,
    stage: str = "e2e",
    extra_seconds: Callable[[str], float] = lambda doc_id: 0.0,
) -> tuple[ModelRun, dict[str, Outcome]]:
    """
    Every document with `model` as the primary. `client_for(doc_id)` gives
    the model client for that document (recording or replay);
    `after_document(doc_id)` runs after each, e.g. to save its cassette;
    `on_final(doc_id, final)` gets the graph's final state (None when the
    run failed), for evals that look at the record itself; `on_unit(doc_id,
    final, outcome, seconds)` runs last, to keep the finished document. A
    CassetteMiss is raised at once: a replay that can't answer is not a
    result. An infrastructure error (evals.progress) raises RunStopped
    before anything of the document is kept. `stage` picks the graph
    (STAGES); `extra_seconds(doc_id)` is model time spent outside this run
    (reused page readings), added to the document's time.
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
            model_health.reset()
            llm._client = client_for(doc_id)
            llm._digests.clear()
            start = time.monotonic()
            try:
                final = run_one(doc_id, user_id, dataset_dir, stage)
            except CassetteMiss:
                raise
            except Exception as e:  # the run failed: everything is missed
                progress.stop_if_infrastructure(e)
                seconds = time.monotonic() - start + extra_seconds(doc_id)
                rec, trans = scores_of(doc_id, None, None, stage, dataset_dir)
                run.documents.append(DocumentResult(doc_id, rec, trans, seconds))
                outcomes[doc_id] = Outcome("failed", [], None, type(e).__name__)
                on_final(doc_id, None)
                after_document(doc_id)
                if on_unit is not None:
                    on_unit(doc_id, None, outcomes[doc_id], seconds)
                continue
            seconds = time.monotonic() - start + extra_seconds(doc_id)
            record = final.get("record")
            rec, trans = scores_of(doc_id, record, pages_of(final), stage, dataset_dir)
            run.documents.append(DocumentResult(doc_id, rec, trans, seconds))
            routing = sorted({h.signal for h in final.get("signals", []) if h.routes})
            role = final.get("parse_model_role") if final.get("record_parsed") else None
            # A vision run ends nowhere a clinician would see it.
            end = None if stage == "vision" else final["end_state"]
            outcomes[doc_id] = Outcome(end, routing, role)
            on_final(doc_id, final)
            after_document(doc_id)
            if on_unit is not None:
                on_unit(doc_id, final, outcomes[doc_id], seconds)
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

    def __init__(self, admin_url: str | None = None, name: str = EVAL_DB_NAME):
        if name == "med_sum":
            raise ValueError("the eval runner never uses med_sum")
        admin_url = admin_url or settings.eval_admin_url()
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
            "INSERT INTO staff_users (org_id, email, full_name) VALUES (%s, %s, %s) "
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


def add_resume_arguments(parser: argparse.ArgumentParser) -> None:
    names = parser.add_mutually_exclusive_group()
    names.add_argument("--label", help="the run's name (default: date and mode)")
    names.add_argument(
        "--resume", metavar="NAME", help="carry on the named run where it stopped"
    )


def open_progress(kind: str, label: str, resume: bool, **fingerprint):
    """The run's Progress, or None after printing why it can't start."""
    try:
        return progress.Progress.open(
            label, progress.fingerprint(kind=kind, **fingerprint), resume=resume
        )
    except progress.ResumeRefused as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return None


def live_digests(client, models: list[str], live: bool) -> dict | None:
    """The installed digests; None (after saying why) when a live run can't
    read them, since a live run needs Ollama and its fingerprint needs them."""
    digests = progress.installed_digests(client, models)
    missing = [m for m, d in digests.items() if d is None]
    if live and missing:
        print(
            f"FAIL: no installed digest for {', '.join(missing)}; is Ollama "
            "running and the model pulled?",
            file=sys.stderr,
        )
        return None
    return digests


def replay_digests(models: list[str], installed: dict) -> dict | None:
    """
    The model build each cassette must have been recorded with: the digests
    of the latest committed live run (its history entry), checked against
    the installed build where Ollama answers. None, after saying why, when
    the installed build is not the recorded one: a replay would then only
    reproduce a model that is no longer the one in use. CI, with no Ollama,
    checks the cassettes against the history entry alone. See
    docs/adr/0009-measured-evals.md.
    """
    recorded: dict[str, str] = {}
    for entry in history.load(history.HISTORY_DIR):
        if entry.get("mode") == "live":
            recorded.update({m: d for m, d in entry.get("digests", {}).items() if d})
    expected = {}
    for model in models:
        mine, theirs = installed.get(model), recorded.get(model)
        if mine and theirs and mine != theirs:
            print(
                f"FAIL replay: {model} is installed as {mine}, but the latest "
                f"live run recorded {theirs}; re-record with a live run",
                file=sys.stderr,
            )
            return None
        if mine or theirs:
            expected[model] = mine or theirs
    return expected


def stopped(e: progress.RunStopped, label: str) -> int:
    print(
        f"STOPPED: {e}. Finished units are kept; carry on with --resume {label}",
        file=sys.stderr,
    )
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call Ollama, record")
    mode.add_argument("--replay", action="store_true", help="answer from cassettes")
    parser.add_argument("--stage", choices=STAGES, default="e2e")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--docs", help="comma-separated ids, e.g. d02,d09")
    parser.add_argument("--split", choices=["dev", "test", "held_out"])
    add_resume_arguments(parser)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cassettes", type=Path, default=CASSETTE_DIR)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args(argv)
    stage = args.stage

    models = args.models or [llm.PRIMARY_MODEL, llm.FALLBACK_MODEL]
    doc_ids = select(args.docs, args.split)
    mode = "live" if args.live else "replay"
    default = f"{time.strftime('%Y-%m-%d')}-{mode}"
    if stage != "e2e":
        default += f"-{stage}"
    label = args.resume or args.label or default
    live_client = llm._client
    installed = live_digests(live_client, models, args.live)
    if installed is None:
        return 2
    expect = {} if args.live else replay_digests(models, installed)
    if expect is None:
        return 2
    units = [progress.unit_id(m, d) for m in models for d in doc_ids]
    run_progress = open_progress(
        "run", label, args.resume is not None, models=models, units=units,
        dataset=progress.json_hash(manifest()),
        digests=installed,
        selection={"mode": mode, "split": args.split, "stage": stage},
    )  # fmt: skip
    if run_progress is None:
        return 2
    with EvalDatabase() as db:
        for model in models:
            cassettes: dict[str, Cassette] = {}
            reused: dict[str, ReusingClient] = {}

            def client_for(
                doc_id: str, model=model, cassettes=cassettes, reused=reused
            ):
                path = cassette_path(model, doc_id, args.cassettes, stage)
                if args.live:
                    cassettes[doc_id] = Cassette()
                    recording = RecordingClient(live_client, cassettes[doc_id])
                    earlier = earlier_cassette(model, doc_id, args.cassettes)
                    if stage != "e2e" or earlier is None:
                        return recording
                    reused[doc_id] = ReusingClient(
                        earlier, recording, expect_digests=installed
                    )
                    return reused[doc_id]
                if not path.exists():
                    raise CassetteMiss(f"{model}: no cassette {path}; re-record")
                return ReplayClient(Cassette.load(path), expect_digests=expect)

            def save(doc_id: str, model=model, cassettes=cassettes):
                if args.live:
                    path = cassette_path(model, doc_id, args.cassettes, stage)
                    cassettes[doc_id].save(path)

            def earlier(doc_id: str, reused=reused) -> float:
                return reused[doc_id].reused_seconds if doc_id in reused else 0.0

            def keep(
                doc_id, final, outcome, seconds, model=model, cassettes=cassettes,
                reused=reused,
            ):  # fmt: skip
                digests = cassettes[doc_id].digests() if doc_id in cassettes else {}
                n = reused[doc_id].reused if doc_id in reused else 0
                run_progress.save(
                    progress.unit_id(model, doc_id),
                    unit_data(final, outcome, seconds, digests=digests, reused=n),
                    seconds=seconds,
                    end=outcome.end_state,
                )

            todo = [
                d for d in doc_ids
                if run_progress.load(progress.unit_id(model, d)) is None
            ]  # fmt: skip
            try:
                run_model(
                    model,
                    todo,
                    user_id=db.user_id,
                    client_for=client_for,
                    after_document=save,
                    on_unit=keep,
                    stage=stage,
                    extra_seconds=earlier,
                )
            except CassetteMiss as e:
                print(f"FAIL replay: {e}", file=sys.stderr)
                return 2
            except progress.RunStopped as e:
                return stopped(e, label)
            print(f"{model}: {len(doc_ids)} documents")

    # The report is built from the progress files, so a resumed run's report
    # is the one an uninterrupted run would have written.
    runs, outcomes, digests = [], {}, {}
    for model in models:
        run, out = ModelRun(model), {}
        for doc_id in doc_ids:
            data = run_progress.load(progress.unit_id(model, doc_id))
            result, out[doc_id] = result_of(doc_id, data, stage=stage)
            run.documents.append(result)
            digests.update(data.get("digests") or {})
        runs.append(run)
        outcomes[model] = out
    report = build_report(
        label, runs, manifest(), outcomes=_as_dicts(outcomes), stage=stage
    )
    kept = args.live and not args.no_history
    name = history.run_name(label, report["generated_at"])
    out_dir = args.out or (history.REPORTS_DIR / name if kept else RESULTS_DIR / label)
    json_path, md_path = write_report(report, out_dir)
    print(f"wrote {json_path} and {md_path}")
    if kept:
        data = history.entry(
            report, mode=mode, digests=digests, report_path=_relative(out_dir)
        )
        print(f"history: {history.write_entry(data, history.HISTORY_DIR)}")
    run_progress.finish()
    return 0


def _relative(path: Path) -> str | None:
    """The report's place relative to scripts/evals, as the history keeps it;
    None for a report written elsewhere (--out), so no machine path is
    committed."""
    try:
        return path.resolve().relative_to(DATASET_DIR.parent.resolve()).as_posix()
    except ValueError:
        return None


def _as_dicts(outcomes: dict[str, dict[str, Outcome]]) -> dict:
    return {
        model: {doc: o.__dict__ for doc, o in docs.items()}
        for model, docs in outcomes.items()
    }


if __name__ == "__main__":
    sys.exit(main())
