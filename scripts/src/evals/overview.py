"""
The overview eval: the patient summary's few sentences, written by each
model from facts that code computed from the answer keys, so a reading or
parsing error never reaches them.

    python -m evals.overview --live   [--model M ...] [--patients a,b]
                                      [--label NAME | --resume NAME]
                                      [--out DIR] [--no-keep]
    python -m evals.overview --replay [same options]
    python -m evals.overview --verdicts FILE --report DIR

Each patient of the dataset is one unit per model: the patient's
documents in upload order, each with its answer key as the record and the
manifest's expected end state as its status, made into facts
(summary/facts.py) and given to the model as the service does
(summary/overview.py), retry and check included.

Code scores each overview:
- **shown**: it passed the deterministic check and would reach the page;
- **attempts** and what the check found unknown (counts only);
- **attention named**: of the tests the facts list for attention (the
  latest value of a test with a flag), how many the overview names.

What code can't score (does every sentence follow from the facts, is
anything a clinician needs first left out, does the whole mislead) is
left to a judge. A run writes judge.json (each shown overview beside its
facts) and judge.md (the rubric); the judge, a separate session reading
only those files, writes verdicts in the format judge.md gives, and
`--verdicts FILE --report DIR` checks them against the packet and adds
their counts to that report. A judge's verdicts are reported, never
gated on: the deterministic check decides what is shown.
See docs/adr/0009-measured-evals.md.

Replies are recorded as cassettes under the cassette directory's
overview/; a run saves each unit and resumes like evals.run. A live run's
report goes to scripts/evals/reports/<name>/, a replay's (or with
--no-keep) to the gitignored scripts/evals/results/<label>/.
"""

import argparse
import json
import re
import statistics
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import llm
import model_health
from dataset.build import DATASET_DIR
from evals import history, progress
from evals.cassettes import Cassette, CassetteMiss, RecordingClient, ReplayClient
from evals.keys import answer_key, documents, manifest
from evals.run import (
    CASSETTE_DIR,
    RESULTS_DIR,
    add_resume_arguments,
    cassette_path,
    live_digests,
    open_progress,
    replay_digests,
    stopped,
)
from evals.stats import wilson
from summary.build import OVERVIEW_PROMPT_FILE
from summary.facts import Facts, SummaryInput, build_facts
from summary.overview import facts_text, write_overview

REPORT_VERSION = 1
STAGE = "overview"  # the cassette directory's part for this eval
FIRST_UPLOAD = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

RUBRIC = """\
# Judging the patient overviews

You are judging short overviews of a patient's records, each written by a
small local model from a list of facts. The facts were computed by code
and are correct; judge each overview against its facts only, never
against what you know of medicine or of the patient.

For each item of judge.json:

1. Split the overview into its sentences. For each sentence, `supported`
   is true when everything it says follows from the facts: the values,
   the tests or medicines they belong to, dates and their order, flags
   and trends as the facts give them. Otherwise false, with `why` saying
   in a few words what does not follow.
2. `omissions`: what a clinician would need first and the overview leaves
   out, in a few words each: a fact listed for attention, an allergy, the
   latest prescription. Not every fact belongs in a few sentences; list
   only what a clinician reading the overview alone would be wrong
   without. An empty list is fine.
3. `misleading`: true when the overview as a whole would lead a clinician
   to a wrong reading (a flagged value made to sound normal, a trend in
   the wrong direction, the wrong order of events, one document's value
   given as current), even if each sentence is supported on its own.

Write verdicts.json:

    {"judge": "<who judged>",
     "verdicts": [
       {"id": "<item id>",
        "sentences": [{"text": "...", "supported": true, "why": ""}],
        "omissions": ["..."],
        "misleading": false}
     ]}

One verdict per item, every item judged, nothing else in the file.
"""


# -------------------------------------------------------------------
# The facts of each patient
# -------------------------------------------------------------------
def patients(dataset_dir: Path = DATASET_DIR) -> list[str]:
    """The dataset's patients that have at least one document, in order."""
    held = {d["patient"] for d in documents(dataset_dir).values()}
    return [p["key"] for p in manifest(dataset_dir)["patients"] if p["key"] in held]


def patient_inputs(patient: str, dataset_dir: Path = DATASET_DIR) -> list[SummaryInput]:
    """
    The patient's documents as the summary job would load them: in upload
    order, one day apart, with the answer key as the record and the
    manifest's expected end state as the status.
    """
    entries = documents(dataset_dir)
    order = manifest(dataset_dir).get("upload_order") or sorted(entries)
    ids = [d for d in order if entries[d]["patient"] == patient]
    return [
        SummaryInput(
            document_id=f"doc-{doc_id}",
            name=entries[doc_id]["file"].split("/")[-1],
            uploaded_at=FIRST_UPLOAD + timedelta(days=n),
            status=entries[doc_id]["expected_end_state"],
            record=answer_key(doc_id, dataset_dir),
        )
        for n, doc_id in enumerate(ids)
    ]


def patient_facts(patient: str, dataset_dir: Path = DATASET_DIR) -> Facts:
    return build_facts(patient_inputs(patient, dataset_dir))


def attention_names(facts: Facts) -> list[str]:
    """The test names of the facts listed for attention, each once."""
    names = []
    for f in facts.attention:
        name = getattr(getattr(f.item, "printed", None), "test_name", None)
        if name and name not in names:
            names.append(name)
    return names


def named(name: str, text: str) -> bool:
    """
    Whether the overview names a test, in any case: its whole name, or a
    name printed with a short form in brackets ("Blood Urea Nitrogen
    (BUN)") by either part.
    """
    forms = [name]
    short = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", name.strip())
    if short:
        forms += [short.group(1), short.group(2)]
    for form in forms:
        words = r"\s+".join(re.escape(w) for w in form.split())
        if re.search(rf"(?<!\w){words}(?!\w)", text, re.IGNORECASE):
            return True
    return False


# -------------------------------------------------------------------
# One unit
# -------------------------------------------------------------------
@contextmanager
def as_primary(model: str):
    """`model` as the primary, the other as the fallback, as the service runs."""
    other = llm.FALLBACK_MODEL if model != llm.FALLBACK_MODEL else llm.PRIMARY_MODEL
    saved_models, saved_client = dict(llm._MODELS), llm._client
    llm._MODELS.update({"primary": model, "fallback": other})
    try:
        yield
    finally:
        llm._MODELS.clear()
        llm._MODELS.update(saved_models)
        llm._client = saved_client
        model_health.reset()


def run_one(patient: str, prompt: str, dataset_dir: Path = DATASET_DIR) -> dict:
    """
    One patient's overview, written and checked; what its unit keeps. No
    model answering at all is an outage, so it stops the run
    (progress.RunStopped) rather than being scored.
    """
    model_health.reset()
    llm._digests.clear()
    facts = patient_facts(patient, dataset_dir)
    start = time.monotonic()
    overview = write_overview(facts, prompt)
    seconds = time.monotonic() - start
    if overview.outcome == "no_model":
        raise progress.RunStopped("no model answered the overview")
    names = attention_names(facts)
    text = overview.text or ""
    check = overview.check
    return {
        "patient": patient,
        "outcome": overview.outcome,
        "text": overview.text,
        "attempts": overview.attempts,
        "unknown_numbers": check.unknown_numbers if check else 0,
        "unknown_words": check.unknown_words if check else 0,
        "attention": len(names),
        "attention_named": sum(named(n, text) for n in names),
        "seconds": seconds,
    }


# -------------------------------------------------------------------
# The report
# -------------------------------------------------------------------
def _model_row(units: list[dict]) -> dict:
    n = len(units)
    shown = [u for u in units if u["outcome"] == "shown"]
    times = [u["seconds"] for u in units if u.get("seconds") is not None]
    return {
        "overviews": n,
        "shown": wilson(len(shown), n).as_dict(),
        "rejected": sum(u["outcome"] == "rejected" for u in units),
        "empty": sum(u["outcome"] == "empty" for u in units),
        "attempts": sum(u["attempts"] for u in units),
        "attention_named": wilson(
            sum(u["attention_named"] for u in shown),
            sum(u["attention"] for u in shown),
        ).as_dict(),
        "seconds_per_overview": statistics.median(times) if times else None,
        "units": units,
    }


def build_report(
    label: str, results: dict[str, list[dict]], generated_at: datetime | None = None
) -> dict:
    """The report of a run: per model, its counts and every unit."""
    generated_at = generated_at or datetime.now(timezone.utc)
    return {
        "report_version": REPORT_VERSION,
        "eval": "overview",
        "label": label,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "dataset": manifest().get("name"),
        "models": {m: _model_row(units) for m, units in results.items()},
        "judge": None,
    }


def item_id(model: str, patient: str) -> str:
    return f"{model}/{patient}"


def judge_packet(report: dict, dataset_dir: Path = DATASET_DIR) -> dict:
    """Every shown overview beside the facts it was written from."""
    items = []
    for model, row in report["models"].items():
        for u in row["units"]:
            if u["outcome"] != "shown":
                continue
            facts = facts_text(patient_facts(u["patient"], dataset_dir))
            items.append({
                "id": item_id(model, u["patient"]),
                "facts": str(facts),
                "overview": u["text"],
            })  # fmt: skip
    return {"label": report["label"], "items": items}


class VerdictError(ValueError):
    """Verdicts that don't answer the packet in the rubric's format."""


def _check_verdict(v: dict) -> None:
    if not isinstance(v.get("sentences"), list) or not v["sentences"]:
        raise VerdictError(f"{v.get('id')}: no sentences judged")
    for s in v["sentences"]:
        if not isinstance(s, dict) or not isinstance(s.get("supported"), bool):
            raise VerdictError(f"{v['id']}: a sentence without a true/false verdict")
    if not isinstance(v.get("omissions"), list):
        raise VerdictError(f"{v['id']}: omissions is not a list")
    if not isinstance(v.get("misleading"), bool):
        raise VerdictError(f"{v['id']}: misleading is not true or false")


def apply_verdicts(report: dict, packet: dict, verdicts: dict) -> dict:
    """
    The report with the judge's counts per model. Every packet item must
    have exactly one verdict and no verdict may name another item.
    """
    if not isinstance(verdicts.get("judge"), str) or not verdicts["judge"].strip():
        raise VerdictError("the verdicts don't say who judged")
    wanted = {i["id"] for i in packet["items"]}
    got: dict[str, dict] = {}
    for v in verdicts.get("verdicts", []):
        if v.get("id") not in wanted:
            raise VerdictError(f"a verdict for {v.get('id')!r}, which is not an item")
        if v["id"] in got:
            raise VerdictError(f"{v['id']}: judged twice")
        _check_verdict(v)
        got[v["id"]] = v
    missing = sorted(wanted - set(got))
    if missing:
        raise VerdictError(f"not judged: {', '.join(missing)}")
    per_model = {}
    for model in report["models"]:
        mine = [v for i, v in got.items() if i.split("/", 1)[0] == model]
        sentences = [s for v in mine for s in v["sentences"]]
        per_model[model] = {
            "overviews": len(mine),
            "sentences_supported": wilson(
                sum(s["supported"] for s in sentences), len(sentences)
            ).as_dict(),
            "all_supported": wilson(
                sum(all(s["supported"] for s in v["sentences"]) for v in mine),
                len(mine),
            ).as_dict(),
            "misleading": wilson(
                sum(v["misleading"] for v in mine), len(mine)
            ).as_dict(),
            "omissions": sum(len(v["omissions"]) for v in mine),
        }
    return {
        **report,
        "judge": {
            "judge": verdicts["judge"],
            "models": per_model,
            "verdicts": [got[i] for i in sorted(got)],
        },
    }


def _interval(d: dict) -> str:
    return wilson(d["hits"], d["total"]).text() if d["total"] else "-"


def render_markdown(report: dict) -> str:
    names = list(report["models"])
    out = [f"# Overview eval: {report['label']}", ""]
    out.append(
        f"Generated {report['generated_at']} on dataset {report['dataset']}; "
        "facts from the answer keys; models: "
        + (", ".join(f"`{m}`" for m in names) or "none")
        + "."
    )
    if not names:
        return "\n".join(out) + "\n"
    rows = [
        ("Overviews", lambda r: str(r["overviews"])),
        ("Shown (passed the check)", lambda r: _interval(r["shown"])),
        ("Rejected after the retry", lambda r: str(r["rejected"])),
        ("Empty", lambda r: str(r["empty"])),
        ("Model replies", lambda r: str(r["attempts"])),
        ("Attention tests named, shown", lambda r: _interval(r["attention_named"])),
        ("Median time per overview", lambda r: _seconds(r["seconds_per_overview"])),
    ]
    out += ["", "## Code's scores", ""]
    out += _table(["Metric", *names], [
        [label, *(get(report["models"][n]) for n in names)] for label, get in rows
    ])  # fmt: skip
    judge = report.get("judge")
    out += ["", "## Judge", ""]
    if judge is None:
        out.append(
            "Not judged yet: judge.json and judge.md are beside this report; "
            "add the verdicts with `python -m evals.overview --verdicts FILE "
            "--report DIR`."
        )
    else:
        out.append(
            f"Judged by {judge['judge']}. Reported only; the check decides "
            "what is shown."
        )
        jrows = [
            ("Overviews judged", lambda r: str(r["overviews"])),
            ("Sentences supported", lambda r: _interval(r["sentences_supported"])),
            ("Overviews with every sentence supported",
             lambda r: _interval(r["all_supported"])),
            ("Misleading overviews", lambda r: _interval(r["misleading"])),
            ("Omissions", lambda r: str(r["omissions"])),
        ]  # fmt: skip
        out += [""] + _table(["Metric", *names], [
            [label, *(get(judge["models"][n]) if n in judge["models"] else "-"
                      for n in names)]
            for label, get in jrows
        ])  # fmt: skip
    out += ["", "## By patient", ""]
    for name in names:
        out += [f"### `{name}`", ""]
        out += _table(["Patient", "Outcome", "Replies", "Attention named", "Time"], [
            [u["patient"], u["outcome"], str(u["attempts"]),
             f"{u['attention_named']}/{u['attention']}", _seconds(u["seconds"])]
            for u in report["models"][name]["units"]
        ])  # fmt: skip
        out.append("")
    return "\n".join(out) + "\n"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    def cells(row):
        return "| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"

    return [cells(header), "| " + " | ".join("---" for _ in header) + " |",
            *(cells(r) for r in rows)]  # fmt: skip


def _seconds(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f} s"


def write_report(
    report: dict, out_dir: Path, packet: dict | None = None
) -> tuple[Path, Path]:
    """report.json and report.md, and (when given) judge.json and judge.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "report.json", out_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    if packet is not None:
        (out_dir / "judge.json").write_text(
            json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (out_dir / "judge.md").write_text(RUBRIC, encoding="utf-8")
    return json_path, md_path


# -------------------------------------------------------------------
# Command line
# -------------------------------------------------------------------
def add_verdicts(verdicts_path: Path, report_dir: Path) -> int:
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        report = read(report_dir / "report.json")
        packet = read(report_dir / "judge.json")
        judged = apply_verdicts(report, packet, read(verdicts_path))
    except (OSError, json.JSONDecodeError, VerdictError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    write_report(judged, report_dir)
    print(f"added the verdicts of {judged['judge']['judge']} to {report_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.overview")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call Ollama, record")
    mode.add_argument("--replay", action="store_true", help="answer from cassettes")
    mode.add_argument("--verdicts", type=Path, help="a judge's verdicts.json")
    parser.add_argument("--report", type=Path, help="the report the verdicts judge")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--patients", help="comma-separated patient keys")
    add_resume_arguments(parser)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cassettes", type=Path, default=CASSETTE_DIR)
    parser.add_argument("--no-keep", action="store_true")
    args = parser.parse_args(argv)
    if args.verdicts is not None:
        if args.report is None:
            parser.error("--verdicts needs --report DIR")
        return add_verdicts(args.verdicts, args.report)

    models = args.models or [llm.PRIMARY_MODEL, llm.FALLBACK_MODEL]
    every = patients()
    chosen = (
        every if not args.patients else [p.strip() for p in args.patients.split(",")]
    )
    unknown = [p for p in chosen if p not in every]
    if unknown:
        raise SystemExit(f"unknown patients: {', '.join(unknown)}")
    mode = "live" if args.live else "replay"
    label = args.resume or args.label or f"{time.strftime('%Y-%m-%d')}-{mode}-overview"
    live_client = llm._client
    installed = live_digests(live_client, models, args.live)
    if installed is None:
        return 2
    expect = {} if args.live else replay_digests(models, installed)
    if expect is None:
        return 2
    run_progress = open_progress(
        "overview", label, args.resume is not None, models=models,
        units=[progress.unit_id(m, p) for m in models for p in chosen],
        dataset=progress.json_hash(manifest()), digests=installed,
        selection={"mode": mode},
    )  # fmt: skip
    if run_progress is None:
        return 2
    prompt = OVERVIEW_PROMPT_FILE.read_text(encoding="utf-8")
    for model in models:
        for patient in chosen:
            unit = progress.unit_id(model, patient)
            if run_progress.load(unit) is not None:
                continue
            path = cassette_path(model, patient, args.cassettes, STAGE)
            with as_primary(model):
                if args.live:
                    cassette = Cassette()
                    llm._client = RecordingClient(live_client, cassette)
                elif path.exists():
                    llm._client = ReplayClient(
                        Cassette.load(path), expect_digests=expect
                    )
                else:
                    print(f"FAIL replay: {model}: no cassette {path}; re-record",
                          file=sys.stderr)  # fmt: skip
                    return 2
                try:
                    data = run_one(patient, prompt)
                except CassetteMiss as e:
                    print(f"FAIL replay: {e}", file=sys.stderr)
                    return 2
                except progress.RunStopped as e:
                    return stopped(e, label)
            if args.live:
                cassette.save(path)
            run_progress.save(unit, data, seconds=data["seconds"], end=data["outcome"])
        print(f"{model}: {len(chosen)} patients")

    results = {
        m: [run_progress.load(progress.unit_id(m, p)) for p in chosen] for m in models
    }
    report = build_report(label, results)
    kept = args.live and not args.no_keep
    name = history.run_name(label, report["generated_at"])
    out_dir = args.out or (history.REPORTS_DIR / name if kept else RESULTS_DIR / label)
    json_path, md_path = write_report(report, out_dir, judge_packet(report))
    print(f"wrote {json_path} and {md_path}, and judge.json and judge.md to judge")
    run_progress.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
