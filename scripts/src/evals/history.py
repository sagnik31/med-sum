"""
The committed history of eval results: one small JSON file per live run
in scripts/evals/history/, with what produced it (code version, prompt
hashes, model digests) and the headline numbers per model and per split.
Entries hold counts only, so they are small enough to compare
(evals/compare.py). Each one names its full report (every document's scores
and field errors), which a live run writes to the committed
scripts/evals/reports/<name>/ under the same name.

    python -m evals.history     # the scoreboard of every entry, as Markdown
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from dataset.build import DATASET_DIR
from pipeline.prompts import FILES

HISTORY_DIR = DATASET_DIR.parent / "history"
REPORTS_DIR = DATASET_DIR.parent / "reports"
HISTORY_VERSION = 1


def code_version(cwd: Path = DATASET_DIR) -> dict | None:
    """The git commit the code is at, and whether the tree has changes."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True,
            check=True,
        ).stdout.strip()  # fmt: skip
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True,
            text=True, check=True,
        ).stdout  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit, "dirty": bool(status.strip())}


def prompt_hashes() -> dict[str, str]:
    """The SHA-256 of every pipeline prompt file, as the steps record them."""
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sorted(FILES.items())
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
        "dataset": report["dataset"],
        "code": code_version(),
        "prompts": prompt_hashes(),
        "digests": dict(sorted((digests or {}).items())),
        "choice": (report.get("choice") or {}).get("model"),
        "report": report_path,
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
            "dataset": data["dataset"],
            "code": None,
            "prompts": {},
            "digests": {},
            "choice": (data.get("choice") or {}).get("model"),
            "report": None,
            "models": {n: metrics(m) for n, m in data["models"].items()},
        }
    raise ValueError(f"{path} is neither a history entry nor a report")


def _pct(v) -> str:
    return "-" if v is None else f"{v:.1%}"


def _of(c: dict | None) -> str:
    return "-" if not c or not c["total"] else f"{c['hits']}/{c['total']}"


def scoreboard(entries: list[dict]) -> str:
    """Every entry's numbers per model, newest first, as a Markdown table."""
    header = ("| Date | Run | Commit | Model | Field F1 | Unsafe | Value recall "
              "| End state right | Auto-verified | Unsafe in verified | Report |")  # fmt: skip
    lines = [header, "| " + " | ".join("---" for _ in range(11)) + " |"]
    for e in reversed(entries):
        commit = (e.get("code") or {}).get("commit", "")[:7] or "-"
        report = e.get("report") or "-"
        for name, m in e["models"].items():
            r = m.get("routing") or {}
            unsafe = f"{m['unsafe']}/{m['clinical_items']}"
            lines.append(
                f"| {e['generated_at'][:10]} | {e['label']} | {commit} | `{name}` | "
                f"{_pct(m['field_f1'])} | {unsafe} | {_pct(m['value_recall'])} | "
                f"{_of(r.get('end_state_right'))} | {_of(r.get('auto_verified'))} | "
                f"{_of(r.get('unsafe_in_verified'))} | {report} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    entries = load()
    if not entries:
        print(f"no history yet in {HISTORY_DIR}")
        return
    sys.stdout.write(scoreboard(entries))


if __name__ == "__main__":
    main()
