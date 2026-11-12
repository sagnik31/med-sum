"""
The synthetic dataset as the derivation and validator tests use it: answer
keys, the parse output each key implies, the PDF text layers, and the
patient each document was uploaded to.
"""

import json
from functools import cache

from dataset.build import DATASET_DIR
from evals.keys import expected_patient, parse_of, text_layers, with_text_layers
from record.document import DocumentRecord

__all__ = [
    "DOCS", "DOC_IDS", "MANIFEST", "PARSED_IDS", "expected_patient", "key",
    "keyed_record", "parse_of", "text_layers", "with_text_layers",
]  # fmt: skip

MANIFEST = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
DOCS = {d["id"]: d for d in MANIFEST["documents"]}
DOC_IDS = sorted(DOCS)
PARSED_IDS = [d for d in DOC_IDS if DOCS[d]["type"] != "other"]


@cache
def _key_json(doc_id: str) -> str:
    return (DATASET_DIR / DOCS[doc_id]["key"]).read_text(encoding="utf-8")


def key(doc_id: str) -> DocumentRecord:
    """A fresh copy of the answer key, safe to change in a test."""
    return DocumentRecord.model_validate_json(_key_json(doc_id))


def keyed_record(doc_id: str) -> DocumentRecord:
    """The answer key with the PDF's text layer on its pages, as the pipeline has it."""
    record = key(doc_id)
    record.pages = with_text_layers(doc_id, record.pages)
    return record
