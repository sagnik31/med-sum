"""
Eval results as JSON and Markdown: one report per eval run, covering every
model that was run, with the model choice (choose.py), the headline
numbers, a breakdown by the manifest's cases (the section 6 bugs and the
dataset's other situations) and by document, and the errors worth reading.

Pure code: it takes the scores, it doesn't run anything. The JSON holds
everything, including every field error; the Markdown is for reading and
lists only unsafe errors and missed or invented items.
"""

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from evals.choose import Candidate, choose_primary
from evals.score import DocumentScore, summarise
from evals.transcription import TranscriptionScore, summarise_transcriptions

REPORT_VERSION = 1


@dataclass
class DocumentResult:
    document_id: str
    record: DocumentScore
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
) -> dict:
    """Everything the report says, as JSON-ready data."""
    generated_at = generated_at or datetime.now(timezone.utc)
    cases = _cases(manifest)
    models, candidates = {}, []
    for run in runs:
        total = summarise(d.record for d in run.documents)
        transcribed = [d.transcription for d in run.documents if d.transcription]
        trans = summarise_transcriptions(transcribed) if transcribed else None
        candidates.append(Candidate(run.model, total, trans, run.seconds_per_document))
        models[run.model] = {
            "seconds_per_document": run.seconds_per_document,
            "summary": total.as_dict(),
            "transcription": trans.as_dict() if trans else None,
            "cases": {case: _case_row(run, ids) for case, ids in sorted(cases.items())},
            "documents": [
                {
                    "record": d.record.as_dict(),
                    "transcription": (
                        d.transcription.as_dict() if d.transcription else None
                    ),
                    "seconds": d.seconds,
                }
                for d in sorted(run.documents, key=lambda d: d.document_id)
            ],
        }
    choice = choose_primary(candidates) if candidates else None
    return {
        "report_version": REPORT_VERSION,
        "label": label,
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


def _cases(manifest: dict) -> dict[str, set[str]]:
    cases: dict[str, set[str]] = {}
    for doc in manifest["documents"]:
        for case in doc.get("cases", []):
            cases.setdefault(case, set()).add(doc["id"])
    return cases


def _case_row(run: ModelRun, ids: set[str]) -> dict:
    docs = [d for d in run.documents if d.document_id in ids]
    total = summarise(d.record for d in docs)
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
    out = [f"# Eval results: {report['label']}", ""]
    out.append(
        f"Generated {report['generated_at']} on dataset "
        f"{report['dataset'] or 'unnamed'}; models: "
        + (", ".join(f"`{m}`" for m in names) or "none")
        + "."
    )
    out += ["", "## Model choice", ""]
    out += _choice_lines(report["choice"])
    if not names:
        return "\n".join(out) + "\n"

    out += ["", "## Overview", ""]
    out += _table(["Metric", *names], _overview_rows(models, names))

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
            "Unsafe errors",
            lambda m: (
                f"{s(m)['unsafe']} of "
                f"{s(m)['clinical_items']} ({_pct(s(m)['unsafe_rate'])})"
            ),
        ),
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
