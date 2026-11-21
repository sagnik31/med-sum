"""
No `<meta>` from a document reaches a page built by code.

The pages are shown in a sandboxed, script-free frame with a CSP (see
docs/adr/0007-pages-built-by-code.md). Neither stops a
`<meta http-equiv="refresh">` from navigating that frame, so the only
defence is that every value is escaped where it is inserted. These tests
put such a tag in every value a document or a clinician supplies and check
that each page has its own charset meta and no other.
"""

from datetime import datetime, timezone
from enum import Enum

import pytest
from bs4 import BeautifulSoup
from pydantic import BaseModel

from dataset_records import DOCS, key
from record.common import Citation, Correction, PrintedText, Provenance
from record.document import DocumentRecord, PageTranscription
from record.printed import DocumentType
from summary.facts import SummaryInput, build_facts
from summary.overview import Overview
from views.document import document_html
from views.summary import summary_html

META = '<meta http-equiv="refresh" content="0;url=https://evil.test/">'
# Models whose strings are text from the document or from a clinician.
TEXT_MODELS = (PrintedText, Citation, PageTranscription, Correction)
# Strings of those models that are names the code looks up, not text.
NOT_TEXT = {"field"}


def _is_text_model(m: BaseModel) -> bool:
    return isinstance(m, TEXT_MODELS) or type(m).__module__ == "record.printed"


def _text(x) -> bool:
    return isinstance(x, str) and not isinstance(x, Enum)


def poisoned(record: DocumentRecord) -> DocumentRecord:
    """
    The record with META after every printed string, every line and text
    layer of its pages, and every correction a clinician could enter.
    """
    record = record.model_copy(deep=True)
    seen = 0

    def walk(v):
        nonlocal seen
        if isinstance(v, list):
            for i, x in enumerate(v):
                if _text(x):
                    v[i] = x + META
                    seen += 1
                else:
                    walk(x)
        elif isinstance(v, BaseModel):
            text = _is_text_model(v)
            for name in type(v).model_fields:
                x = getattr(v, name)
                if text and _text(x) and name not in NOT_TEXT:
                    object.__setattr__(v, name, x + META)
                    seen += 1
                elif isinstance(x, list) and not text:
                    for y in x:
                        walk(y)
                else:
                    walk(x)

    walk(record)
    assert seen > 0
    return record


def corrected(record: DocumentRecord) -> DocumentRecord:
    """The record with a clinician's correction and note on every item."""
    record = record.model_copy(deep=True)
    at = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for item in record.items():
        item.provenance = Provenance(run_id="r", node="parse")
        item.corrections = [
            Correction(
                field="note",
                original=None,
                corrected="x",
                clinician="c",
                at=at,
                note="checked",
            )
        ]
    return record


def only_own_meta(html: str) -> None:
    metas = BeautifulSoup(html, "html.parser").find_all("meta")
    assert [m.attrs for m in metas] == [{"charset": "UTF-8"}]
    assert "<meta http-equiv" not in html.lower()


@pytest.mark.parametrize("doc_id", sorted(DOCS))
def test_no_meta_from_a_document_reaches_its_page(doc_id):
    record = poisoned(corrected(key(doc_id)))
    html = document_html(record, parsed=record.document.type is not DocumentType.OTHER)
    only_own_meta(html)
    # Shown as text, not dropped.
    assert "&lt;meta http-equiv" in html


def test_no_meta_from_a_document_reaches_the_patient_summary():
    ins = [
        SummaryInput(
            document_id=f"id-{d}",
            name=f"{d}.pdf{META}",
            uploaded_at=datetime(2026, 1, n + 1, tzinfo=timezone.utc),
            status="verified",
            record=poisoned(key(d)),
        )
        for n, d in enumerate(sorted(DOCS))
    ]
    html = summary_html(
        build_facts(ins),
        Overview(f"Stable.{META}", "passed"),
        generated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    only_own_meta(html)
    assert "&lt;meta http-equiv" in html
