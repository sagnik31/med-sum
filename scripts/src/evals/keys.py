"""The synthetic dataset as the evals read it: the manifest and the answer keys."""

import json
from functools import cache
from pathlib import Path

from dataset.build import DATASET_DIR
from record.document import DocumentRecord


@cache
def manifest(dataset_dir: Path = DATASET_DIR) -> dict:
    return json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))


def documents(dataset_dir: Path = DATASET_DIR) -> dict[str, dict]:
    """Manifest entries by document id, in id order."""
    docs = manifest(dataset_dir)["documents"]
    return {d["id"]: d for d in sorted(docs, key=lambda d: d["id"])}


def answer_key(doc_id: str, dataset_dir: Path = DATASET_DIR) -> DocumentRecord:
    """A fresh copy of a document's answer key."""
    entry = documents(dataset_dir)[doc_id]
    text = (dataset_dir / entry["key"]).read_text(encoding="utf-8")
    return DocumentRecord.model_validate_json(text)
