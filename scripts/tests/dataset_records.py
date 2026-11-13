"""
The synthetic dataset as the derivation and validator tests use it: answer
keys, the parse output each key implies, the PDF text layers, and the
patient each document was uploaded to.
"""

import json
from functools import cache

from dataset.build import DATASET_DIR
from evals.keys import expected_patient, parse_of, text_layers, with_text_layers
from pipeline import parts
from record.document import DocumentRecord

__all__ = [
    "DOCS", "DOC_IDS", "HELD_OUT_IDS", "MAIN_IDS", "MAIN_PARSED_IDS", "MANIFEST",
    "PARSED_IDS", "expected_patient", "key", "keyed_record", "parse_of",
    "part_replies", "split_parse", "text_layers", "with_text_layers",
]  # fmt: skip

MANIFEST = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
DOCS = {d["id"]: d for d in MANIFEST["documents"]}
DOC_IDS = sorted(DOCS)
PARSED_IDS = [d for d in DOC_IDS if DOCS[d]["type"] != "other"]
# Held-out documents measure the code; tests that pin its exact output run
# on the main set only, so the held-out set is never what the code is
# fitted to.
HELD_OUT_IDS = [d for d in DOC_IDS if DOCS[d]["split"] == "held_out"]
MAIN_IDS = [d for d in DOC_IDS if d not in HELD_OUT_IDS]
MAIN_PARSED_IDS = [d for d in PARSED_IDS if d not in HELD_OUT_IDS]


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


def _held(part, value) -> bool:
    c = value.citation
    return part.holds(c.page, c.first_line)


def split_parse(parse, plan):
    """
    A whole-document parse cut into the parse each part would give: every
    value and item in the part whose lines it cites. parts.merge of the
    pieces is the parse again (a test checks it over the dataset).
    """
    pieces = []
    for part in plan:
        piece = parse.model_copy(deep=True)
        for holder in (piece.patient, piece.details):
            for name in type(holder).model_fields:
                value = getattr(holder, name)
                if isinstance(value, list):
                    setattr(holder, name, [v for v in value if _held(part, v)])
                elif value is not None and not _held(part, value):
                    setattr(holder, name, None)
        for name in type(piece).model_fields:
            if name not in ("patient", "details"):
                items = [v for v in getattr(piece, name) if _held(part, v)]
                setattr(piece, name, items)
        pieces.append(piece)
    return pieces


def part_replies(doc_id: str) -> list[str]:
    """The model's replies, one per part, that give the answer key's parse."""
    record = key(doc_id)
    plan = parts.plan(record.pages, record.document.type)
    return [p.model_dump_json() for p in split_parse(parse_of(record), plan)]
