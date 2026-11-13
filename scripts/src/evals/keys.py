"""
The synthetic dataset as the evals read it: the manifest, the answer keys,
the parse output each key implies, the PDF text layers, and the patient
each document was uploaded to.
"""

import json
from datetime import date
from functools import cache
from pathlib import Path

import pymupdf

from dataset.build import DATASET_DIR
from record.document import DocumentRecord, PageTranscription
from record.printed import PARSE_MODELS, DocumentType, LabReportParse, PrescriptionParse
from validators import ExpectedPatient


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
def text_layers(doc_id: str, dataset_dir: Path = DATASET_DIR) -> tuple[str, ...] | None:
    """Each page's PDF text, for documents that have a text layer."""
    entry = documents(dataset_dir)[doc_id]
    if not entry["has_text_layer"]:
        return None
    with pymupdf.open(dataset_dir / entry["file"]) as pdf:
        return tuple(page.get_text() for page in pdf)


def with_text_layers(
    doc_id: str, pages: list[PageTranscription], dataset_dir: Path = DATASET_DIR
) -> list[PageTranscription]:
    layers = text_layers(doc_id, dataset_dir)
    if layers is None:
        return pages
    return [p.model_copy(update={"text_layer": t}) for p, t in zip(pages, layers)]


def patient_of(key: str, dataset_dir: Path = DATASET_DIR) -> ExpectedPatient:
    """A dataset patient, by key, with the details a patient record holds."""
    patient = next(p for p in manifest(dataset_dir)["patients"] if p["key"] == key)
    return ExpectedPatient(
        name=patient["name"],
        sex=patient["sex"],
        date_of_birth=date.fromisoformat(patient["date_of_birth"]),
        mrn=patient["mrn"],
    )


def expected_patient(doc_id: str, dataset_dir: Path = DATASET_DIR) -> ExpectedPatient:
    """The patient a document was uploaded to."""
    return patient_of(documents(dataset_dir)[doc_id]["patient"], dataset_dir)


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
