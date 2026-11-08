"""
The synthetic dataset as the derivation and validator tests use it: answer
keys, the parse output each key implies, the PDF text layers, and the
patient each document was uploaded to.
"""

import json
from functools import cache

import pymupdf

from dataset.build import DATASET_DIR
from record.document import DocumentRecord, PageTranscription
from record.printed import PARSE_MODELS, DocumentType, LabReportParse, PrescriptionParse

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


def parse_of(record: DocumentRecord) -> LabReportParse | PrescriptionParse | None:
    """The parse output that produces this record: its printed parts."""
    doc_type = record.document.type
    if doc_type not in PARSE_MODELS:
        return None
    common = {
        "patient": record.patient.printed,
        "details": record.document.printed,
        "remarks": [r.printed for r in record.remarks],
        "conditions": [c.printed for c in record.conditions],
        "vitals": [v.printed for v in record.vitals],
        "allergies": [a.printed for a in record.allergies],
    }
    if doc_type is DocumentType.LAB_REPORT:
        return LabReportParse(
            results=[o.printed for o in record.observations], **common
        )
    return PrescriptionParse(
        medicines=[m.printed for m in record.medications], **common
    )


@cache
def text_layers(doc_id: str) -> tuple[str, ...] | None:
    """Each page's PDF text, for documents that have a text layer."""
    if not DOCS[doc_id]["has_text_layer"]:
        return None
    with pymupdf.open(DATASET_DIR / DOCS[doc_id]["file"]) as pdf:
        return tuple(page.get_text() for page in pdf)


def with_text_layers(doc_id: str, pages: list[PageTranscription]) -> list:
    layers = text_layers(doc_id)
    if layers is None:
        return pages
    return [p.model_copy(update={"text_layer": t}) for p, t in zip(pages, layers)]


def keyed_record(doc_id: str) -> DocumentRecord:
    """The answer key with the PDF's text layer on its pages, as the pipeline has it."""
    record = key(doc_id)
    record.pages = with_text_layers(doc_id, record.pages)
    return record
