"""
The committed history of eval results: one small JSON file per live run
in scripts/evals/history/, with what produced it (code version, prompt
hashes, model digests, generation options with the seed) and the headline numbers per model and per split.
Entries hold counts only, so they are small enough to compare
(evals/compare.py). Each one names its full report (every document's scores
and field errors), which a live run writes to the committed
scripts/evals/reports/<name>/ under the same name.

    python -m evals.history     # the scoreboard of every entry, as Markdown
    python -m evals.history --latest STAGE   # the path of the stage's latest
                                             # live entry; nothing if none
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import llm
from dataset.build import DATASET_DIR
from evals import progress
from evals.model_texts import model_texts, text_files

HISTORY_DIR = DATASET_DIR.parent / "history"
REPORTS_DIR = DATASET_DIR.parent / "reports"
HISTORY_VERSION = 1


def prompt_hashes() -> dict[str, str]:
    """
    The SHA-256 of every text a model is instructed with (evals.model_texts):
    a file's bytes, as the steps record them, else the text in UTF-8.
    """
    files = text_files()
    return {
        name: hashlib.sha256(
            files[name].read_bytes() if name in files else text.encode("utf-8")
        ).hexdigest()
        for name, text in sorted(model_texts().items())
    }


def _counts(d: dict) -> dict:
    return {"hits": d["hits"], "total": d["total"]}


def metrics(model: dict) -> dict:
    """The headline numbers of one model's part of a report."""
    s, t, r = model["summary"], model["transcription"], model.get("routing")
    out = {
        "documents": s["documents"],
        "field_f1": s["all_fields"]["f1"],
        "derived_f1": s["all_derived"]["f1"],
        "medicine_recall": s["medicine_recall"],
        "unsafe": s["unsafe"],
        "clinical_items": s["clinical_items"],
        "hallucinated": s["hallucinated"],
        "missed": s["missed"],
        # Reports before 2026-11-24 have none.
        "clinical_values": (s.get("clinical_values") or {}).get("value"),
        "value_recall": t["values"]["value"] if t else None,
        "cer": t["cer"] if t else None,
        "seconds_per_document": model["seconds_per_document"],
        "splits": {
            split: {
                "documents": row["documents"],
                "field_f1": row["field_f1"],
                "unsafe": _counts(row["unsafe"]),
            }
            for split, row in model.get("splits", {}).items()
        },
        "routing": None,
    }
    if r:
        out["routing"] = {
            "end_state_right": _counts(r["end_state_right"]),
            "auto_verified": _counts(r["auto_verified"]),
            "unsafe_in_verified": _counts(r["unsafe_in_verified"]),
            "fallback_records": r["fallback_records"],
            "failed": r["failed"],
        }
    return out


def restricted(model: dict, ids: list[str]) -> dict:
    """The numbers `evals.compare` reads, of one model's part of a report, over
    only these of its documents: worked out from its per-document scores."""
    docs = [d for d in model["documents"] if _document_id(d) in ids]
    records = [d["record"] for d in docs]

    def rate(counts: list[dict], name: str) -> float | None:
        tp, fp, fn = (sum(c[k] for c in counts) for k in ("tp", "fp", "fn"))
        top, bottom = (tp, tp + fn) if name == "recall" else (2 * tp, 2 * tp + fp + fn)
        return top / bottom if bottom else None

    values = [d["transcription"]["values"] for d in docs if d["transcription"]]
    read = sum(v["total"] for v in values)
    rows = [r for r in model["routing"]["documents"] if r["document"] in ids]
    # "verified" in a report from before no run ended so.
    ended = {r["document"]: r["end_state"] in ("checks_passed", "verified")
             for r in rows}  # fmt: skip
    passed = [d["record"] for d in docs if ended.get(_document_id(d))]
    return {
        "field_f1": rate([r["all_fields"] for r in records], "f1"),
        "derived_f1": rate([r["all_derived"] for r in records], "f1"),
        "medicine_recall": rate([r["items"]["medications"] for r in records], "recall"),
        "value_recall": sum(v["correct"] for v in values) / read if read else None,
        "unsafe": sum(len(r["unsafe"]) for r in records),
        "routing": {
            "end_state_right": {
                "hits": sum(r["end_state_right"] for r in rows),
                "total": len(rows),
            },
            "unsafe_in_verified": {
                "hits": sum(r["unsafe_items"] for r in passed),
                "total": sum(r["checked_items"] for r in passed),
            },
        },
    }


def document_ids(report: dict) -> dict[str, list[str]]:
    """The documents each model of a report ran, sorted."""
    return {
        name: sorted(_document_id(d) for d in m.get("documents", []))
        for name, m in report["models"].items()
    }


def _document_id(d: dict) -> str:
    # Reports before the stages named a document only inside its record.
    return d.get("document_id") or d["record"]["document_id"]


def stage_of(entry: dict) -> str:
    """The stage a history entry or report measured; e2e before the stages."""
    return entry.get("stage") or "e2e"


def _code() -> dict | None:
    """The commit, and whether the tree has changes outside the eval outputs.
    A run writes its own cassettes, reports and results before its entry, so
    counting those would call every live run's code dirty."""
    found = progress.code_fingerprint()
    if found is None:
        return None
    return {"commit": found["commit"], "dirty": found["dirty"]}


def entry(
    report: dict,
    *,
    mode: str,
    digests: dict[str, str | None] | None = None,
    report_path: str | None = None,
) -> dict:
    """The history entry of a report; report_path is where its full report is,
    relative to scripts/evals (e.g. "reports/2026-11-12-live")."""
    return {
        "history_version": HISTORY_VERSION,
        "label": report["label"],
        "generated_at": report["generated_at"],
        "mode": mode,
        "stage": stage_of(report),
        "dataset": report["dataset"],
        "code": _code(),
        "prompts": prompt_hashes(),
        "digests": dict(sorted((digests or {}).items())),
        "generation_options": dict(llm.GENERATION_OPTIONS),
        "choice": (report.get("choice") or {}).get("model"),
        "report": report_path,
        "documents": document_ids(report),
        "models": {name: metrics(m) for name, m in report["models"].items()},
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", text).strip("-") or "run"


def run_name(label: str, generated_at: str) -> str:
    """The name a run's history entry and report share: its day and label,
    without the day twice when the label already starts with it."""
    day, slug = generated_at[:10], _slug(label)
    return slug if slug.startswith(day) else f"{day}-{slug}"


def write_entry(data: dict, directory: Path = HISTORY_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_name(data['label'], data['generated_at'])}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load(directory: Path = HISTORY_DIR) -> list[dict]:
    """Every entry, oldest first."""
    if not directory.exists():
        return []
    entries = [json.loads(p.read_text(encoding="utf-8"))
               for p in directory.glob("*.json")]  # fmt: skip
    return sorted(entries, key=lambda e: e["generated_at"])


def latest(stage: str, directory: Path = HISTORY_DIR) -> Path | None:
    """The latest live entry of a stage, by when it was generated."""
    entries = [
        (e["generated_at"], path)
        for path in directory.glob("*.json")
        if (e := json.loads(path.read_text(encoding="utf-8"))).get("mode") == "live"
        and stage_of(e) == stage
    ]
    return max(entries)[1] if entries else None


def read(path: Path) -> dict:
    """A history entry, or the entry of a report.json."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "history_version" in data:
        return data
    if "report_version" in data:
        return {
            "history_version": HISTORY_VERSION,
            "label": data["label"],
            "generated_at": data["generated_at"],
            "mode": "report",
            "stage": stage_of(data),
            "dataset": data["dataset"],
            "code": None,
            "prompts": {},
            "digests": {},
            "generation_options": {},
            "choice": (data.get("choice") or {}).get("model"),
            "report": None,
            "documents": document_ids(data),
            "models": {n: metrics(m) for n, m in data["models"].items()},
        }
    raise ValueError(f"{path} is neither a history entry nor a report")


def _pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def _of(c: dict | None) -> str:
    return "-" if not c or not c["total"] else f"{c['hits']}/{c['total']}"


def scoreboard(entries: list[dict]) -> str:
    """Every entry's numbers per model, newest first, as a Markdown table."""
    header = ("| Date | Run | Stage | Commit | Model | Field F1 | Unsafe "
              "| Value recall | End state right | Passed every check | Unsafe in those "
              "| Report |")  # fmt: skip
    lines = [header, "| " + " | ".join("---" for _ in range(12)) + " |"]
    for e in reversed(entries):
        commit = (e.get("code") or {}).get("commit", "")[:7] or "-"
        report = e.get("report") or "-"
        for name, m in e["models"].items():
            r = m.get("routing") or {}
            unsafe = f"{m['unsafe']}/{m['clinical_items']}"
            lines.append(
                f"| {e['generated_at'][:10]} | {e['label']} | {stage_of(e)} | "
                f"{commit} | `{name}` | "
                f"{_pct(m['field_f1'])} | {unsafe} | {_pct(m['value_recall'])} | "
                f"{_of(r.get('end_state_right'))} | {_of(r.get('auto_verified'))} | "
                f"{_of(r.get('unsafe_in_verified'))} | {report} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="The history of eval results.")
    parser.add_argument("--latest", metavar="STAGE")
    args = parser.parse_args(argv)
    if args.latest:
        path = latest(args.latest)
        if path is not None:
            print(path.as_posix())
        return
    entries = load()
    if not entries:
        print(f"no history yet in {HISTORY_DIR}")
        return
    sys.stdout.write(scoreboard(entries))


if __name__ == "__main__":
    main()
