"""
The frontend's tests run on a synthetic record (frontend/src/record/fixtures).
These checks keep it a valid stored record, so the frontend's tests follow
the real schema: a change to the record's shape fails here until the
fixture, and what the document page shows, are updated with it.
"""

import json
from pathlib import Path

import pytest

from record.document import DocumentRecord

FIXTURES = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "record" / "fixtures"
)


def fixture_files():
    return sorted(FIXTURES.glob("*.json"))


def test_there_is_a_record_fixture():
    assert fixture_files(), f"no fixtures in {FIXTURES}"


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.name)
def test_each_fixture_is_a_valid_record(path):
    record = DocumentRecord.model_validate(json.loads(path.read_text("utf-8")))
    # Every citation's quote is the text of its lines, as the pipeline stores it.
    for item in record.items():
        c = item.printed.citation
        assert record.pages[c.page - 1].quote(c.first_line, c.last_line) == c.quote
