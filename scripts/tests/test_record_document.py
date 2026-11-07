"""The stored record: round trips, versioning and what code guarantees."""

import json
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from record.common import SCHEMA_VERSION, Correction, ItemStatus, Provenance
from record.document import (
    DateSource,
    DocumentDates,
    DocumentRecord,
    PageTranscription,
    numbered_document,
)
from record_examples import CBC_LINES, cbc_record, provenance

REVIEWED_AT = datetime(2025, 3, 4, 10, 0, tzinfo=timezone.utc)


def test_the_example_record_is_valid():
    record = DocumentRecord.model_validate(cbc_record())

    assert record.schema_version == SCHEMA_VERSION == 1
    assert record.observations[1].derived.converted.value == 150000
    assert record.document.derived.document_date == date(2025, 3, 3)


def test_a_record_survives_a_json_round_trip():
    record = DocumentRecord.model_validate(cbc_record())

    again = DocumentRecord.model_validate_json(record.model_dump_json())

    assert again == record


def test_the_stored_json_keeps_printed_text_untouched():
    data = json.loads(DocumentRecord.model_validate(cbc_record()).model_dump_json())

    assert data["observations"][0]["printed"]["value_text"] == "10.4"
    assert data["pages"][0]["lines"] == CBC_LINES


def test_an_answer_key_may_leave_out_provenance_and_derived_values():
    data = cbc_record()
    for item in data["observations"]:
        del item["provenance"]
        del item["derived"]
    del data["document"]["provenance"]

    record = DocumentRecord.model_validate(data)

    assert record.observations[0].derived.value_num is None
    assert record.observations[0].provenance is None


@pytest.mark.parametrize("version", [0, 2, "1"])
def test_other_schema_versions_are_refused(version):
    with pytest.raises(ValidationError, match="schema_version"):
        DocumentRecord.model_validate({**cbc_record(), "schema_version": version})


def test_unknown_fields_are_refused_anywhere():
    data = cbc_record()
    data["observations"][0]["derived"]["trend"] = "worsening"

    with pytest.raises(ValidationError, match="trend"):
        DocumentRecord.model_validate(data)


# -------------------------------------------------------------------
# What code guarantees
# -------------------------------------------------------------------
def test_item_ids_are_unique_across_item_kinds():
    data = cbc_record()
    data["remarks"][0]["id"] = "obs-1"

    with pytest.raises(ValidationError, match="duplicate item id 'obs-1'"):
        DocumentRecord.model_validate(data)


def test_a_test_remark_points_at_its_observation():
    data = cbc_record()
    data["remarks"][0]["printed"].update(
        scope="test", test_name="Haemoglobin", panel=None
    )
    data["remarks"][0]["derived"] = {"observation_id": "obs-1"}

    record = DocumentRecord.model_validate(data)

    assert [r.id for r in record.remarks_for("obs-1")] == ["rem-1"]
    assert record.remarks_for("obs-2") == []


def test_a_remark_cannot_point_at_an_unknown_observation():
    data = cbc_record()
    data["remarks"][0]["printed"].update(
        scope="test", test_name="Haemoglobin", panel=None
    )
    data["remarks"][0]["derived"] = {"observation_id": "obs-9"}

    with pytest.raises(ValidationError, match="unknown observation 'obs-9'"):
        DocumentRecord.model_validate(data)


def test_a_panel_remark_cannot_be_pinned_on_a_test():
    # Decision 25: a remark is attached to a test only when it is on its row.
    data = cbc_record()
    data["remarks"][0]["derived"] = {"observation_id": "obs-1"}

    with pytest.raises(ValidationError, match="is on the panel"):
        DocumentRecord.model_validate(data)


@pytest.mark.parametrize(
    "pages, page_count, message",
    [
        ([2], 1, "numbered 1..n"),
        ([1, 1], 2, "numbered 1..n"),
        ([2, 1], 2, "numbered 1..n"),
        ([1], 2, "2 pages but 1 transcriptions"),
    ],
)
def test_pages_match_the_document(pages, page_count, message):
    data = cbc_record()
    data["pages"] = [{"page": n, "lines": ["x"]} for n in pages]
    data["document"]["page_count"] = page_count

    with pytest.raises(ValidationError, match=message):
        DocumentRecord.model_validate(data)


def test_items_lists_every_kind_in_a_fixed_order():
    record = DocumentRecord.model_validate(cbc_record())

    assert [i.id for i in record.items()] == [
        "obs-1",
        "obs-2",
        "rem-1",
        "rem-2",
        "alg-1",
    ]


# -------------------------------------------------------------------
# Document date (decision 13)
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "dates",
    [
        {"report_date": date(2025, 3, 3), "document_date": date(2025, 3, 3),
         "date_source": "report_date"},
        {"collection_date": date(2025, 3, 1), "document_date": date(2025, 3, 1),
         "date_source": "collection_date"},
        {"prescription_date": date(2025, 2, 1), "document_date": date(2025, 2, 1),
         "date_source": "prescription_date"},
        {},
    ],
    ids=["report", "collection", "prescription", "undated"],
)  # fmt: skip
def test_the_document_date_follows_its_source(dates):
    DocumentDates(**dates)


@pytest.mark.parametrize(
    "dates, message",
    [
        ({"document_date": date(2025, 3, 3)}, "undated document has no"),
        ({"report_date": date(2025, 3, 3), "document_date": date(2025, 3, 1),
          "date_source": "report_date"}, "must equal report_date"),
        ({"collection_date": date(2025, 3, 1), "date_source": "collection_date"},
         "must equal collection_date"),
    ],
)  # fmt: skip
def test_a_document_date_that_does_not_follow_its_source(dates, message):
    with pytest.raises(ValidationError, match=message):
        DocumentDates(**dates)


def test_an_undated_document_is_the_default():
    assert DocumentDates().date_source is DateSource.UNDATED


# -------------------------------------------------------------------
# Provenance and corrections
# -------------------------------------------------------------------
def test_item_statuses_are_those_of_the_plan():
    assert [s.value for s in ItemStatus] == [
        "extracted",
        "validated",
        "low_confidence",
        "clinician_confirmed",
        "clinician_corrected",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"status": "validated"},
        {"status": "low_confidence", "low_confidence_reasons": ["text layer differs"]},
        {"status": "clinician_confirmed", "low_confidence_reasons": ["fallback model"],
         "reviewed_by": "Dr. S. Iyer", "reviewed_at": REVIEWED_AT},
        {"status": "clinician_corrected", "reviewed_by": "Dr. S. Iyer",
         "reviewed_at": REVIEWED_AT},
    ],
)  # fmt: skip
def test_consistent_provenance(overrides):
    Provenance(**provenance(**overrides))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"status": "low_confidence"}, "needs its reasons"),
        ({"low_confidence_reasons": ["unreadable"]}, "cannot be extracted"),
        ({"status": "validated", "low_confidence_reasons": ["x"]}, "cannot be validated"),
        ({"status": "clinician_confirmed"}, "reviewed_by and reviewed_at"),
        ({"reviewed_by": "Dr. S. Iyer", "reviewed_at": REVIEWED_AT},
         "reviewed_by and reviewed_at"),
    ],
)  # fmt: skip
def test_inconsistent_provenance(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Provenance(**provenance(**overrides))


def test_a_correction_is_stored_next_to_the_original_reading():
    data = cbc_record()
    hb = data["observations"][0]
    hb["provenance"] = provenance(
        status="clinician_corrected",
        low_confidence_reasons=["value not in the text layer"],
        reviewed_by="Dr. S. Iyer",
        reviewed_at=REVIEWED_AT.isoformat(),
    )
    hb["corrections"] = [
        {
            "field": "value_text",
            "original": "10.4",
            "corrected": "10.1",
            "clinician": "Dr. S. Iyer",
            "at": REVIEWED_AT.isoformat(),
        }
    ]

    record = DocumentRecord.model_validate(data)

    observation = record.observations[0]
    assert observation.printed.value_text == "10.4"
    assert observation.corrections == [
        Correction(
            field="value_text",
            original="10.4",
            corrected="10.1",
            clinician="Dr. S. Iyer",
            at=REVIEWED_AT,
        )
    ]


# -------------------------------------------------------------------
# Transcriptions: the citation anchor
# -------------------------------------------------------------------
def test_numbered_text_is_what_the_parse_step_sees():
    page = PageTranscription(page=1, lines=["# Lab", "", "| Hb | 10.4 |"])

    assert page.numbered_text() == "1| # Lab\n2| \n3| | Hb | 10.4 |"


def test_numbered_document_heads_each_page():
    pages = [
        PageTranscription(page=1, lines=["first"]),
        PageTranscription(page=2, lines=["second"]),
    ]

    assert (
        numbered_document(pages)
        == "=== Page 1 ===\n1| first\n\n=== Page 2 ===\n1| second"
    )


def test_quote_returns_the_cited_lines():
    page = PageTranscription(page=1, lines=CBC_LINES)

    assert page.quote(8, 8) == "| Haemoglobin | 10.4 L | g/dL | 12.0 - 15.0 |"
    assert page.quote(10, 11) == "\n".join(CBC_LINES[9:11])


@pytest.mark.parametrize("first, last", [(0, 1), (3, 2), (12, 13)])
def test_quote_refuses_lines_that_are_not_on_the_page(first, last):
    with pytest.raises(IndexError, match="not on page 1"):
        PageTranscription(page=1, lines=CBC_LINES).quote(first, last)
