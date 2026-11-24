"""
Eval results as JSON and Markdown: one report per eval run, covering every
model that was run, with the model choice (choose.py), the headline
numbers, a breakdown by the manifest's cases (the early summaries' mistakes
and the dataset's other situations) and by document, and the errors worth
reading.

Given each document's outcome (its end state and routing signals), it also
scores routing against the manifest's expected end state, and gives the
risk-coverage line: the share of documents that ended checks_passed
(the ones auto-verification would let through with no clinician), and the
unsafe errors inside that share. Runs ended verified before
docs/adr/0013-model-choice-after-the-first-live-evals.md; the JSON keeps
the keys auto_verified and unsafe_in_verified, so reports compare across
the change. Counts
carry Wilson intervals (stats.py), and the dev documents (manifest
`split`) are reported apart from the test documents.

A report is of one stage (evals/run.py): a vision report has only
transcription scores and chooses no model (it says which models pass the
value-recall gate); a reasoning report has no transcription scores, and
its choice leaves that gate to the vision report; an e2e report has both.

Pure code: it takes the scores, it doesn't run anything. The JSON holds
everything, including every field error; the Markdown is for reading and
lists only unsafe errors and missed or invented items.
"""

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from evals.choose import MIN_VALUE_RECALL, Candidate, choose_primary
from evals.score import DocumentScore, summarise
from evals.stats import wilson
from evals.transcription import TranscriptionScore, summarise_transcriptions

REPORT_VERSION = 3


@dataclass
class DocumentResult:
    document_id: str
    record: DocumentScore | None  # None in a vision run, which makes no record
    transcription: TranscriptionScore | None = None
    seconds: float | None = None  # wall time for the document's run


@dataclass
class ModelRun:
    model: str
    documents: list[DocumentResult] = field(default_factory=list)

    @property
    def seconds_per_document(self) -> float | None:
        times = [d.seconds for d in self.documents if d.seconds is not None]
        return statistics.median(times) if times else None


# -------------------------------------------------------------------
# The report as data
# -------------------------------------------------------------------
def build_report(
    label: str,
    runs: list[ModelRun],
    manifest: dict,
    generated_at: datetime | None = None,
    outcomes: dict[str, dict[str, dict]] | None = None,
    stage: str = "e2e",
) -> dict:
    """
    Everything the report says, as JSON-ready data. `outcomes` (model ->
    document -> {end_state, signals, record_model_role, error}) adds
    routing and risk coverage; without it (or in a vision report, whose
    runs end before routing) those are left out.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    outcomes = {} if stage == "vision" else outcomes or {}
    entries = {d["id"]: d for d in manifest["documents"]}
    cases = _cases(manifest)
    models, candidates, gate = {}, [], {}
    for run in runs:
        total = summarise(_records(run.documents))
        transcribed = [d.transcription for d in run.documents if d.transcription]
        trans = summarise_transcriptions(transcribed) if transcribed else None
        candidates.append(Candidate(run.model, total, trans, run.seconds_per_document))
        recall = trans.values.value if trans else None
        gate[run.model] = recall is not None and recall >= MIN_VALUE_RECALL
        models[run.model] = {
            "seconds_per_document": run.seconds_per_document,
            "summary": total.as_dict(),
            "transcription": trans.as_dict() if trans else None,
            "cases": {case: _case_row(run, ids) for case, ids in sorted(cases.items())},
            "splits": _splits(run, entries),
            "routing": _routing(run, entries, outcomes.get(run.model)),
            "documents": [
                {
                    "document_id": d.document_id,
                    "record": d.record.as_dict() if d.record else None,
                    "transcription": (
                        d.transcription.as_dict() if d.transcription else None
                    ),
                    "seconds": d.seconds,
                    "outcome": outcomes.get(run.model, {}).get(d.document_id),
                }
                for d in sorted(run.documents, key=lambda d: d.document_id)
            ],
        }
    choice = None
    if candidates and stage != "vision":
        choice = choose_primary(candidates, require_transcription=stage != "reasoning")
    return {
        "report_version": REPORT_VERSION,
        "stage": stage,
        "label": label,
        # A vision report chooses no model: which ones read enough values.
        "value_recall_gate": gate if stage == "vision" else None,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "dataset": manifest.get("name"),
        "choice": None
        if choice is None
        else {
            "model": choice.model,
            "reason": choice.reason,
            "qualified": list(choice.qualified),
            "excluded": choice.excluded,
        },
        "cases": {case: sorted(ids) for case, ids in sorted(cases.items())},
        "models": models,
    }


def _records(documents) -> list[DocumentScore]:
    return [d.record for d in documents if d.record is not None]


def _cases(manifest: dict) -> dict[str, set[str]]:
    cases: dict[str, set[str]] = {}
    for doc in manifest["documents"]:
        for case in doc.get("cases", []):
            cases.setdefault(case, set()).add(doc["id"])
    return cases


def _unsafe(total) -> dict:
    """Items with an unsafe error, of the items checked: a share that can't
    pass 100%, unlike errors per clinical item (two errors in one item)."""
    return wilson(total.unsafe_items, total.checked_items).as_dict()


def _split_of(entries: dict[str, dict], doc_id: str) -> str:
    return entries.get(doc_id, {}).get("split", "test")


def _splits(run: ModelRun, entries: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for split in sorted({_split_of(entries, d.document_id) for d in run.documents}):
        docs = [d for d in run.documents if _split_of(entries, d.document_id) == split]
        total = summarise(_records(docs))
        transcribed = [d.transcription for d in docs if d.transcription]
        trans = summarise_transcriptions(transcribed) if transcribed else None
        out[split] = {
            "documents": total.documents,
            "field_f1": total.all_fields.f1,
            "unsafe": _unsafe(total),
            "value_recall": trans.values.value if trans else None,
        }
    return out


def _routing(run: ModelRun, entries: dict[str, dict], outcomes: dict | None):
    """End states against the manifest, and the risk-coverage line."""
    if not outcomes:
        return None
    rows, verified = [], []
    for d in sorted(run.documents, key=lambda d: d.document_id):
        o = outcomes.get(d.document_id)
        if o is None:
            continue
        entry = entries.get(d.document_id, {})
        expected = entry.get("expected_end_state")
        expected_signals = sorted(entry.get("expected_signals", []))
        rows.append({
            "document": d.document_id,
            "expected_end_state": expected,
            "end_state": o["end_state"],
            "expected_signals": expected_signals,
            "signals": o["signals"],
            "end_state_right": o["end_state"] == expected,
            "signals_right": o["signals"] == expected_signals,
            "record_model_role": o.get("record_model_role"),
            "error": o.get("error"),
        })  # fmt: skip
        if o["end_state"] == "checks_passed":
            verified.append(d)
    n = len(rows)
    in_verified = summarise(_records(verified))
    return {
        "end_state_right": wilson(sum(r["end_state_right"] for r in rows), n).as_dict(),
        "signals_right": wilson(sum(r["signals_right"] for r in rows), n).as_dict(),
        "auto_verified": wilson(len(verified), n).as_dict(),
        "unsafe_in_verified": _unsafe(in_verified),
        "fallback_records": sum(r["record_model_role"] == "fallback" for r in rows),
        "failed": sum(r["end_state"] == "failed" for r in rows),
        "documents": rows,
    }


def _case_row(run: ModelRun, ids: set[str]) -> dict:
    docs = [d for d in run.documents if d.document_id in ids]
    total = summarise(_records(docs))
    transcribed = [d.transcription for d in docs if d.transcription]
    trans = summarise_transcriptions(transcribed) if transcribed else None
    return {
        "documents": total.documents,
        "field_f1": total.all_fields.f1,
        "unsafe": total.unsafe,
        "hallucinated": total.hallucinated,
        "missed": total.missed,
        "remark_attribution": total.remark_attribution.as_dict(),
        "frequency": total.frequency.as_dict(),
        "value_recall": trans.values.value if trans else None,
    }


# -------------------------------------------------------------------
# Markdown
# -------------------------------------------------------------------
def render_markdown(report: dict) -> str:
    models = report["models"]
    names = list(models)
    stage = report.get("stage", "e2e")
    out = [f"# Eval results: {report['label']}", ""]
    out.append(
        f"Generated {report['generated_at']} on dataset "
        f"{report['dataset'] or 'unnamed'}; stage {stage} ({STAGE_TEXT[stage]}); "
        "models: " + (", ".join(f"`{m}`" for m in names) or "none") + "."
    )
    for model, left_out in report.get("not_measured", {}).items():
        if left_out:
            out += ["", f"**Not measured** (stale recordings), `{model}`: "
                    f"{', '.join(left_out)}."]  # fmt: skip
    out += ["", "## Model choice", ""]
    if stage == "vision":
        out += _gate_lines(report.get("value_recall_gate") or {})
    else:
        out += _choice_lines(report["choice"])
    if not names:
        return "\n".join(out) + "\n"

    out += ["", "## Overview", ""]
    rows = _overview_rows(models, names)
    if stage == "vision":
        rows = [r for r in rows if r[0] in VISION_ROWS]
    elif stage == "reasoning":
        rows = [r for r in rows if not r[0].startswith(("Transcription", "Layout",
                                                         "Unreadable"))]  # fmt: skip
    out += _table(["Metric", *names], rows)

    if any(models[n]["routing"] for n in names):
        out += ["", "## Routing and risk coverage", ""]
        out.append(
            "Documents that pass every check wait for a clinician's sign-off "
            "until the safety gate passes; with auto-verification they would "
            "reach no clinician, so the unsafe errors inside them are what the "
            "checks let through."
        )
        out += [""] + _table(["Metric", *names], _routing_rows(models, names))
        wrong = [
            f"- `{n}` {r['document']}: {r['end_state']} {r['signals']}, expected "
            f"{r['expected_end_state']} {r['expected_signals']}"
            for n in names
            for r in (models[n]["routing"] or {}).get("documents", [])
            if not r["end_state_right"]
        ]
        if wrong:
            out += ["", "Documents ending in the wrong state:", "", *wrong]

    splits = sorted({s for n in names for s in models[n]["splits"]})
    if len(splits) > 1:
        out += ["", "## By split", ""]
        out.append(
            "Dev documents were used while writing and checking the prompts; "
            "the test documents were not."
        )
        rows = []
        for split in splits:
            for n in names:
                row = models[n]["splits"].get(split)
                if row is None:
                    continue
                rows.append([
                    split, f"`{n}`", str(row["documents"]), _pct(row["field_f1"]),
                    _interval(row["unsafe"]), _pct(row["value_recall"]),
                ])  # fmt: skip
        out += [""] + _table(
            ["Split", "Model", "Docs", "Field F1", "Unsafe", "Value recall"], rows
        )

    out += ["", "## By case", ""]
    out.append(
        "Field F1, unsafe errors, and (where the case has them) remark "
        "attribution, frequency exactness and transcription value recall, "
        "over the documents that show each case."
    )
    for name in names:
        out += ["", f"### `{name}`", ""]
        rows = []
        for case, row in models[name]["cases"].items():
            rows.append([
                case, str(row["documents"]), _pct(row["field_f1"]), str(row["unsafe"]),
                _ratio(row["remark_attribution"]), _ratio(row["frequency"]),
                _pct(row["value_recall"]),
            ])  # fmt: skip
        out += _table(["Case", "Docs", "Field F1", "Unsafe", "Attribution",
                       "Frequency", "Value recall"], rows)  # fmt: skip

    out += ["", "## By document", ""]
    if stage == "vision":
        out += _vision_documents(models, names)
        out.append(
            "Every line, layout difference and missing value is in the JSON report."
        )
        return "\n".join(out) + "\n"
    for name in names:
        out += [f"### `{name}`", ""]
        rows = []
        for d in models[name]["documents"]:
            r, t = d["record"], d["transcription"]
            rows.append([
                r["document_id"], r["type_got"] or "(no record)",
                "yes" if r["type_correct"] else "**no**",
                _pct(r["all_fields"]["f1"]), str(len(r["unsafe"])),
                str(len(r["missed"])), str(len(r["hallucinated"])),
                _pct(t["cer"]) if t else "-", _pct(t["values"]["value"]) if t else "-",
                _seconds(d["seconds"]),
            ])  # fmt: skip
        out += _table(["Doc", "Type", "Type right", "Field F1", "Unsafe", "Missed",
                       "Invented", "CER", "Value recall", "Time"], rows)  # fmt: skip
        out.append("")

    out += ["## Errors", ""]
    any_errors = False
    for name in names:
        lines = _error_lines(models[name]["documents"])
        if lines:
            any_errors = True
            out += [f"### `{name}`", "", *lines, ""]
    if not any_errors:
        out += ["No unsafe errors, missed items or invented items.", ""]
    out.append(
        "Every field error, layout difference and missing transcribed value "
        "is in the JSON report."
    )
    return "\n".join(out) + "\n"


STAGE_TEXT = {
    "vision": "reading the pages only",
    "reasoning": "from the expected transcriptions",
    "e2e": "the whole pipeline",
}
VISION_ROWS = {
    "Documents",
    "Transcription CER",
    "Transcription line F1",
    "Transcription value recall",
    "Layout kept",
    "Unreadable lines",
    "Median time per document",
}


def _gate_lines(gate: dict[str, bool]) -> list[str]:
    lines = [
        "A vision run chooses no model. The primary must also read at least "
        f"{MIN_VALUE_RECALL:.0%} of the answer keys' printed values:"
    ]
    for model, ok in gate.items():
        lines.append(f"- `{model}`: {'passes' if ok else '**fails**'}.")
    return lines


def _vision_documents(models: dict, names: list[str]) -> list[str]:
    out = []
    for name in names:
        out += [f"### `{name}`", ""]
        rows = []
        for d in models[name]["documents"]:
            t = d["transcription"]
            rows.append([
                d["document_id"], _pct(t["cer"]), _pct(t["lines"]["f1"]),
                _ratio(t["values"]), _ratio(t["layout"]), str(t["unreadable"]),
                _seconds(d["seconds"]),
            ])  # fmt: skip
        out += _table(["Doc", "CER", "Line F1", "Value recall", "Layout",
                       "Unreadable", "Time"], rows)  # fmt: skip
        out.append("")
    return out


def _choice_lines(choice: dict | None) -> list[str]:
    if choice is None:
        return ["No models were run."]
    lines = []
    if choice["model"] is None:
        lines.append(f"**No model chosen:** {choice['reason']}.")
    else:
        lines.append(f"**Primary: `{choice['model']}`**, {choice['reason']}.")
    for model, why in choice["excluded"].items():
        lines.append(f"- `{model}` excluded: {why}.")
    return lines


def _overview_rows(models: dict, names: list[str]) -> list[list[str]]:
    def row(label, get):
        return [label, *(get(models[n]) for n in names)]

    s = lambda m: m["summary"]  # noqa: E731
    t = lambda m: m["transcription"]  # noqa: E731
    return [
        row("Documents", lambda m: str(s(m)["documents"])),
        row("Type right", lambda m: _ratio(s(m)["types"])),
        row("Field precision", lambda m: _pct(s(m)["all_fields"]["precision"])),
        row("Field recall", lambda m: _pct(s(m)["all_fields"]["recall"])),
        row("Field F1", lambda m: _pct(s(m)["all_fields"]["f1"])),
        row("Derived F1", lambda m: _pct(s(m)["all_derived"]["f1"])),
        row("Medicine recall", lambda m: _pct(s(m)["medicine_recall"])),
        row("Citations right", lambda m: _ratio(s(m)["citations"])),
        row("Remark attribution", lambda m: _ratio(s(m)["remark_attribution"])),
        row("Frequency exactness", lambda m: _ratio(s(m)["frequency"])),
        row(
            "Items with an unsafe error",
            lambda m: _interval(
                wilson(s(m)["unsafe_items"], s(m)["checked_items"]).as_dict()
            ),
        ),
        row("Clinical values given", lambda m: _ratio(s(m)["clinical_values"])),
        row("Invented items", lambda m: str(s(m)["hallucinated"])),
        row("Missed items", lambda m: str(s(m)["missed"])),
        row("Transcription CER", lambda m: _pct(t(m)["cer"]) if t(m) else "-"),
        row(
            "Transcription line F1",
            lambda m: _pct(t(m)["lines"]["f1"]) if t(m) else "-",
        ),
        row(
            "Transcription value recall",
            lambda m: _ratio(t(m)["values"]) if t(m) else "-",
        ),
        row("Layout kept", lambda m: _ratio(t(m)["layout"]) if t(m) else "-"),
        row("Unreadable lines", lambda m: str(t(m)["unreadable"]) if t(m) else "-"),
        row("Median time per document", lambda m: _seconds(m["seconds_per_document"])),
    ]


def _routing_rows(models: dict, names: list[str]) -> list[list[str]]:
    def row(label, get):
        cells = [get(models[n]["routing"]) if models[n]["routing"] else "-"
                 for n in names]  # fmt: skip
        return [label, *cells]

    return [
        row("End state right", lambda r: _interval(r["end_state_right"])),
        row("Signals as expected", lambda r: _interval(r["signals_right"])),
        row(
            "Checks passed (would auto-verify)", lambda r: _interval(r["auto_verified"])
        ),
        row(
            "Items with an unsafe error, checks passed",
            lambda r: _interval(r["unsafe_in_verified"]),
        ),
        row("Records by the fallback", lambda r: str(r["fallback_records"])),
        row("Failed runs", lambda r: str(r["failed"])),
    ]


def _interval(d: dict) -> str:
    return wilson(d["hits"], d["total"]).text() if d["total"] else "-"


def _error_lines(documents: list[dict]) -> list[str]:
    lines = []
    for d in documents:
        r = d["record"]
        doc = r["document_id"]
        for u in r["unsafe"]:
            if u["key_id"] is None:
                lines.append(f"- {doc} unsafe: invented {u['group']} item "
                             f"{_code(u['got'])}")  # fmt: skip
            else:
                lines.append(
                    f"- {doc} unsafe: {u['group']} {u['key_id']} {u['field']} "
                    f"expected {_code(u['expected'])}, got {_code(u['got'])}"
                )
        for m in r["missed"]:
            lines.append(f"- {doc} missed {m['group']} {m['id']} {_code(m['label'])}")
        for h in r["hallucinated"]:
            if not any(u["pred_id"] == h["id"] for u in r["unsafe"]):
                lines.append(f"- {doc} invented {h['group']} {_code(h['label'])}")
    return lines


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    def cells(row):
        return "| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"

    return [cells(header), "| " + " | ".join("---" for _ in header) + " |",
            *(cells(r) for r in rows)]  # fmt: skip


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.1%}"


def _ratio(r: dict) -> str:
    if not r["total"]:
        return "-"
    return f"{r['correct']}/{r['total']} ({r['value']:.1%})"


def _seconds(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f} s"


def _code(v) -> str:
    return "(none)" if v is None else f"`{v}`"


# -------------------------------------------------------------------
def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write report.json and report.md into out_dir; returns both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "report.json", out_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
