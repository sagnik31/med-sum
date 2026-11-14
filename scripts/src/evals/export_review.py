"""
Export a clinician-reviewed document as a candidate eval case: a copy of
the uploaded file, a key built from the reviewed record (every correction
applied, as the summary reads it) and a case file saying where it came
from and what the clinician changed (items, fields and signals; never the
values themselves, which are in the key).

    python -m evals.export_review <document_id> <out_dir> [--database-url URL]

It writes <out_dir>/<document_id>/ and refuses to overwrite one. A
candidate is not a dataset case yet: someone checks the key by hand
before adding it to scripts/evals/dataset/.

Development only (MEDSUM_ENV=dev), where every document is synthetic: the
export copies a patient's document and values out of the database, which
must never happen to a real patient's. It reads record_reviews, which the
AI service's own role is not granted, so it runs as the database owner
(the development default). See docs/adr/0011-clinician-review.md.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from pipeline import checkpoints
from record.document import DocumentRecord
from record.review import reviewed_view
from settings import DEV, SettingsError


class ExportError(RuntimeError):
    """The document can't be exported as a case; the message says why."""


def _load(conn, document_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.storage_path, r.record, r.run_id::text, i.status"
            " FROM documents d"
            " LEFT JOIN document_records r ON r.document_id = d.id"
            " LEFT JOIN insights i ON i.document_id = d.id"
            " WHERE d.id = %s AND d.deleted_at IS NULL",
            (document_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ExportError("no such document")
        path, record, run_id, status = row
        if record is None or status != "verified":
            raise ExportError("only a verified document with a record can be exported")
        cur.execute(
            "SELECT item_id, field, action, signals, audit, reviewed_at"
            " FROM record_reviews WHERE document_id = %s AND run_id IS NOT DISTINCT FROM %s"
            " ORDER BY reviewed_at, id",
            (document_id, run_id),
        )
        reviews = [
            {"item_id": i, "field": f, "action": a, "signals": list(s), "audit": au,
             "reviewed_at": at.isoformat()}
            for i, f, a, s, au, at in cur.fetchall()
        ]  # fmt: skip
    if not reviews:
        raise ExportError("no clinician has reviewed this document's record")
    return {"path": Path(path), "record": record, "run_id": run_id, "reviews": reviews}


def _without_corrections(value):
    """The record as a key: the corrected values stand as the reading."""
    if isinstance(value, dict):
        return {
            k: ([] if k == "corrections" else _without_corrections(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_without_corrections(v) for v in value]
    return value


def key_of(record: dict) -> dict:
    view = reviewed_view(DocumentRecord.model_validate(record))
    return _without_corrections(view.model_dump(mode="json"))


def export(
    conn, document_id: str, out_dir: Path, *, now: datetime | None = None
) -> Path:
    """Write the candidate case; returns its directory."""
    loaded = _load(conn, document_id)
    if not loaded["path"].is_file():
        raise ExportError("the document's file is missing")
    target = Path(out_dir) / document_id
    if target.exists():
        raise ExportError(f"{target} exists already")
    key = key_of(loaded["record"])
    target.mkdir(parents=True)
    file_name = "document" + loaded["path"].suffix.lower()
    shutil.copyfile(loaded["path"], target / file_name)
    (target / "key.json").write_text(
        json.dumps(key, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    corrected = [r for r in loaded["reviews"] if r["action"] == "correct"]
    case = {
        "status": "candidate",
        "source": "clinician_review",
        "document_id": document_id,
        "run_id": loaded["run_id"],
        "schema_version": key["schema_version"],
        "file": file_name,
        "key": "key.json",
        "corrected_fields": len(corrected),
        "signals": sorted({s for r in loaded["reviews"] for s in r["signals"]}),
        "reviews": loaded["reviews"],
        "exported_at": (now or datetime.now(timezone.utc)).isoformat(),
        "check_before_use": (
            "Check every value of the key against the file by hand before"
            " adding this case to the dataset. Synthetic documents only."
        ),
    }
    (target / "case.json").write_text(
        json.dumps(case, indent=2) + "\n", encoding="utf-8"
    )
    return target


def main(argv: list[str] | None = None, environ=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.export_review")
    parser.add_argument("document_id")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if environ.get("MEDSUM_ENV", "").strip() != DEV:
        print(
            "refused: exporting a document is allowed only under MEDSUM_ENV=dev,"
            " where every document is synthetic",
            file=sys.stderr,
        )
        return 2
    try:
        url = args.database_url or checkpoints.database_url()
        with psycopg.connect(url, autocommit=True) as conn:
            target = export(conn, args.document_id, args.out_dir)
    except (ExportError, SettingsError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
