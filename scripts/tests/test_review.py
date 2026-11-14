"""
Clinician review of a record (record.review) and the AI service's
/internal/review-apply. No database, no model.
"""

from datetime import date, datetime, timezone

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import insights_service as service
from record.common import ItemStatus
from record.document import DateSource, DocumentRecord
from record.review import (
    ReviewError,
    ReviewRequest,
    apply_review,
    flagged,
    read_field,
    reviewed_view,
    signal_ids,
)
from record_examples import cbc_record, provenance
from summary.facts import SummaryInput, build_facts
from summary.overview import Overview
from validators.findings import Validator
from views.document import document_html, review_note
from views.summary import summary_html

AT = datetime(2025, 3, 5, 10, 0, tzinfo=timezone.utc)
TODAY = date(2025, 3, 6)
DR = "Dr. Meera Iyer"
TOKEN = "test-internal-token"

HB_REASONS = [
    "LC-10 [flag_printed]: the printed flag and the flag from the range disagree",
    "LC-03 [value_text]: the value differs from the PDF's own text",
]


def flagged_record() -> DocumentRecord:
    """The CBC record with haemoglobin and the document awaiting review."""
    data = cbc_record()
    data["observations"][0]["provenance"] = provenance(
        status="low_confidence", low_confidence_reasons=HB_REASONS
    )
    data["document"]["provenance"] = provenance(
        status="low_confidence",
        low_confidence_reasons=["LC-12: the document is undated"],
    )
    for item in data["remarks"] + data["allergies"]:
        item["provenance"] = provenance(status="validated")
    return DocumentRecord.model_validate(data)


def req(action="correct", item_id="obs-1", field=None, value=None, **kw):
    return ReviewRequest(
        action=action, item_id=item_id, field=field, value=value,
        reviewer=DR, at=AT, **kw,
    )  # fmt: skip


def obs(record, item_id="obs-1"):
    return next(o for o in record.observations if o.id == item_id)


# -------------------------------------------------------------------
# Confirming
# -------------------------------------------------------------------
def test_a_confirmation_marks_the_item_reviewed_and_keeps_its_reasons():
    record = flagged_record()
    assert flagged(record) == 2

    result = apply_review(record, req("confirm"), today=TODAY)

    p = obs(result.record).provenance
    assert p.status is ItemStatus.CLINICIAN_CONFIRMED
    assert (p.reviewed_by, p.reviewed_at) == (DR, AT)
    assert p.low_confidence_reasons == HB_REASONS  # what the review answered
    assert result.signals == ["LC-10", "LC-03"]
    assert result.flagged == 1
    assert result.findings == [] and result.original is None
    assert obs(result.record).printed == obs(record).printed


def test_an_unflagged_item_can_be_confirmed_too():
    result = apply_review(flagged_record(), req("confirm", item_id="obs-2"))
    assert (
        obs(result.record, "obs-2").provenance.status is ItemStatus.CLINICIAN_CONFIRMED
    )
    assert result.signals == []
    assert result.flagged == 2


def test_the_document_s_own_flags_are_confirmed_as_the_document():
    result = apply_review(flagged_record(), req("confirm", item_id="document"))
    assert result.record.document.provenance.status is ItemStatus.CLINICIAN_CONFIRMED
    assert result.signals == ["LC-12"]


def test_once_everything_flagged_is_reviewed_nothing_awaits():
    record = apply_review(flagged_record(), req("confirm", item_id="document")).record
    result = apply_review(record, req(field="value_text", value="11.4"), today=TODAY)
    assert result.flagged == 0


def test_a_confirmation_takes_no_field_or_value():
    with pytest.raises(ReviewError):
        apply_review(flagged_record(), req("confirm", field="value_text"))


def test_confirming_a_corrected_item_keeps_it_corrected():
    record = apply_review(
        flagged_record(), req(field="value_text", value="11.4")
    ).record
    result = apply_review(record, req("confirm"))
    assert obs(result.record).provenance.status is ItemStatus.CLINICIAN_CORRECTED
    assert len(obs(result.record).corrections) == 1


# -------------------------------------------------------------------
# Correcting
# -------------------------------------------------------------------
def test_a_correction_never_overwrites_what_was_read():
    record = flagged_record()
    result = apply_review(record, req(field="value_text", value="11.4", note="re-read"))

    item = obs(result.record)
    assert item.printed == obs(record).printed  # 10.4, as the model read it
    assert item.derived == obs(record).derived
    [c] = item.corrections
    assert (c.field, c.original, c.corrected, c.clinician, c.at, c.note) == (
        "value_text", "10.4", "11.4", DR, AT, "re-read",
    )  # fmt: skip
    assert item.provenance.status is ItemStatus.CLINICIAN_CORRECTED
    assert result.original == "10.4"


def test_the_reviewed_view_shows_the_correction_with_values_derived_again():
    record = apply_review(
        flagged_record(), req(field="value_text", value="13.1")
    ).record
    view = reviewed_view(record)
    item = obs(view)
    assert item.printed.value_text == "13.1"
    assert item.derived.value_num == 13.1
    assert item.derived.flag_computed.value == "normal"
    assert item.corrections == obs(record).corrections
    assert item.provenance == obs(record).provenance


def test_the_latest_correction_of_a_field_wins():
    record = flagged_record()
    for value in ("11.4", "11.9"):
        record = apply_review(record, req(field="value_text", value=value)).record
    assert [c.original for c in obs(record).corrections] == ["10.4", "10.4"]
    assert obs(reviewed_view(record)).printed.value_text == "11.9"


def test_a_corrected_value_goes_through_the_value_checks():
    # 13.1 is within 12.0-15.0, but "L" is printed: the printed flag and the
    # computed one now disagree. Reported, not refused: the document may
    # itself be wrong.
    result = apply_review(flagged_record(), req(field="value_text", value="13.1"))
    assert [f.validator for f in result.findings] == [Validator.FLAG_AGREEMENT]
    assert obs(result.record).corrections  # saved all the same


def test_a_corrected_value_is_not_checked_against_the_transcription():
    # 10.9 is printed nowhere on the page; the transcription checks would
    # flag it, but the clinician read the source document.
    result = apply_review(flagged_record(), req(field="value_text", value="10.9"))
    assert result.findings == []


def test_a_corrected_range_is_parsed_again():
    record = apply_review(
        flagged_record(), req(field="reference_ranges[0]", value="9.0 - 12.0")
    ).record
    view = obs(reviewed_view(record))
    assert (view.derived.ranges[0].low, view.derived.ranges[0].high) == (9.0, 12.0)
    assert view.derived.flag_computed.value == "normal"


def test_a_corrected_unit_that_was_not_printed_reads_as_nothing():
    result = apply_review(
        flagged_record(), req("correct", "obs-1", "flag_printed", "H")
    )
    assert result.original == "L"
    data = cbc_record()
    data["observations"][1]["printed"]["unit"] = None
    record = DocumentRecord.model_validate(data)
    result = apply_review(record, req(item_id="obs-2", field="unit", value="lakh/cumm"))
    assert result.original is None


def test_a_corrected_report_date_moves_the_document_date():
    record = apply_review(
        flagged_record(),
        req(item_id="document", field="report_date", value="04/03/2025 09:00"),
    ).record
    assert record.document.printed.report_date.text == "03/03/2025 14:30"
    derived = reviewed_view(record).document.derived
    assert derived.document_date == date(2025, 3, 4)
    assert derived.date_source is DateSource.REPORT_DATE


def test_a_corrected_date_that_is_not_a_date_is_reported():
    result = apply_review(
        flagged_record(),
        req(item_id="document", field="report_date", value="sometime"),
        today=TODAY,
    )
    assert [(f.validator, f.field) for f in result.findings] == [
        (Validator.DATES, "report_date")
    ]


def test_a_corrected_clinician_and_patient_name_are_kept_beside_the_reading():
    record = apply_review(
        flagged_record(),
        req(item_id="document", field="clinicians[0]", value="Ref. by: Dr. K. Mehra"),
    ).record
    record = apply_review(
        record, req(item_id="patient", field="name", value="Asha Rau")
    ).record
    view = reviewed_view(record)
    assert view.document.printed.clinicians[0].text == "Ref. by: Dr. K. Mehra"
    assert view.patient.printed.name.text == "Asha Rau"
    assert record.patient.printed.name.text == "Asha Rao"


def test_a_corrected_test_name_moves_its_remark():
    data = cbc_record()
    data["remarks"][0]["printed"].update(scope="test", test_name="Haemoglobin")
    data["remarks"][0]["derived"] = {"observation_id": "obs-1"}
    for group in ("observations", "remarks", "allergies"):
        for item in data[group]:
            item["provenance"] = provenance(status="validated")
    record = DocumentRecord.model_validate(data)
    record = apply_review(
        record, req(item_id="obs-1", field="test_name", value="Hb")
    ).record
    view = reviewed_view(record)
    assert view.remarks[0].derived.observation_id is None  # no test of that name now


@pytest.mark.parametrize(
    "item_id, field, value",
    [
        ("obs-1", "citation", "x"),  # where it is printed is not a reading
        ("rem-1", "kind", "advice"),  # a label from a fixed list
        ("obs-1", "reference_ranges", "1 - 2"),  # a list needs an entry
        ("obs-1", "reference_ranges[3]", "1 - 2"),
        ("obs-1", "no_such_field", "x"),
        ("document", "prescription_date", "01/01/2025"),  # nothing was read
        ("obs-9", "value_text", "1"),
        ("obs-1", "value_text", "10.4"),  # unchanged: confirm instead
        ("obs-1", "value_text", "   "),
        ("obs-1", None, "11"),
        ("obs-1", "value_text", "x" * 501),
    ],
)
def test_a_correction_that_can_t_be_applied_is_refused(item_id, field, value):
    with pytest.raises(ReviewError) as err:
        apply_review(flagged_record(), req(item_id=item_id, field=field, value=value))
    # The message names ids and fields, never the value or the reading.
    assert "10.4" not in str(err.value)
    if len(value.strip()) > 1:
        assert value not in str(err.value)


def test_an_item_without_provenance_can_t_be_reviewed():
    data = cbc_record()
    with pytest.raises(ReviewError):
        apply_review(
            DocumentRecord.model_validate(data), req("confirm", item_id="rem-1")
        )


def test_read_field_reads_text_and_printed_text():
    record = flagged_record()
    assert read_field(obs(record).printed, "value_text") == "10.4"
    assert (
        read_field(record.document.printed, "clinicians[0]") == "Ref. by: Dr. K. Mehta"
    )


def test_signal_ids_are_the_distinct_ids_in_order():
    assert signal_ids(["LC-04 [x]: a", "LC-I2: b", "LC-04: c", "no id"]) == [
        "LC-04",
        "LC-I2",
    ]


def test_a_record_without_corrections_is_its_own_view():
    record = flagged_record()
    assert reviewed_view(record) is record


# -------------------------------------------------------------------
# What the pages show
# -------------------------------------------------------------------
def test_the_document_page_shows_the_corrected_value_and_what_was_read():
    record = apply_review(
        flagged_record(), req(field="value_text", value="11.4")
    ).record
    soup = BeautifulSoup(document_html(reviewed_view(record)), "html.parser")
    row = soup.find(attrs={"data-item": "obs-1"})
    assert "11.4" in row.get_text()
    note = soup.find(attrs={"data-reviewed": "clinician_corrected"})
    assert note.get_text() == (
        f"corrected by {DR} on 2025-03-05 (value_text, read as '10.4')"
    )


def test_a_confirmation_is_noted_by_who_and_when():
    record = apply_review(flagged_record(), req("confirm")).record
    assert review_note(obs(record)) == f"confirmed by {DR} on 2025-03-05"


def test_the_summary_says_corrected_by_whom_and_when():
    record = apply_review(
        flagged_record(), req(field="value_text", value="11.4")
    ).record
    ins = [SummaryInput("id-1", "cbc.pdf", AT, "verified", reviewed_view(record))]
    html = summary_html(build_facts(ins), Overview(None, "no_model"), generated_at=AT)
    soup = BeautifulSoup(html, "html.parser")
    tags = soup.find_all(attrs={"data-corrected": "obs-1"})
    assert tags and all(
        t.get_text() == f"corrected by {DR} on 2025-03-05" for t in tags
    )
    assert "11.4" in soup.get_text()


# -------------------------------------------------------------------
# The internal endpoint
# -------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", TOKEN)
    return TestClient(service.app)


def body(**review):
    return {
        "record": flagged_record().model_dump(mode="json"),
        "review": {"action": "correct", "item_id": "obs-1", "reviewer": DR,
                   "at": AT.isoformat(), **review},
    }  # fmt: skip


def test_the_endpoint_returns_the_reviewed_record_and_its_page(client):
    response = client.post(
        "/internal/review-apply",
        json=body(field="value_text", value="13.1"),
        headers={"X-Internal-Token": TOKEN},
    )
    assert response.status_code == 200
    data = response.json()
    record = DocumentRecord.model_validate(data["record"])
    assert obs(record).corrections[0].corrected == "13.1"
    assert data["original"] == "10.4"
    assert data["signals"] == ["LC-10", "LC-03"]
    assert data["flagged"] == 1
    assert [f["validator"] for f in data["findings"]] == ["flag_agreement"]
    assert "13.1" in data["html"]


def test_the_endpoint_refuses_a_review_it_can_t_apply(client):
    response = client.post(
        "/internal/review-apply",
        json=body(field="citation", value="x"),
        headers={"X-Internal-Token": TOKEN},
    )
    assert response.status_code == 422
    assert "citation" in response.json()["detail"]


def test_the_endpoint_refuses_an_invalid_record(client):
    data = body(field="value_text", value="1")
    data["record"]["pages"] = []
    response = client.post(
        "/internal/review-apply", json=data, headers={"X-Internal-Token": TOKEN}
    )
    assert response.status_code == 422


def test_the_endpoint_needs_the_internal_token(client):
    response = client.post("/internal/review-apply", json=body(action="confirm"))
    assert response.status_code == 401


def test_the_endpoint_logs_ids_and_counts_only(client, json_log):
    client.post(
        "/internal/review-apply",
        json=body(field="value_text", value="13.1"),
        headers={"X-Internal-Token": TOKEN},
    )
    text = json_log.getvalue()
    assert '"review_applied"' in text and '"obs-1"' in text
    assert "13.1" not in text and "10.4" not in text and DR not in text
