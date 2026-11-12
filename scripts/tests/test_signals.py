"""
The low-confidence signals (pipeline/signals.py): the catalogue, which
finding raises which signal at which level, the signals read from the run
itself, and how routing hits mark the record. One test (or test case) per
signal.
"""

from datetime import date

import pytest

from dataset_records import DOCS, PARSED_IDS, expected_patient, keyed_record
from observability import stamp
from pipeline import signals
from pipeline.signals import SIGNALS, Kind, SignalHit
from record.common import ItemStatus, Provenance
from record.document import DocumentDates
from record.printed import DocumentType
from validators import Finding, Severity, Validator, validate

LAB, RX, OTHER = DocumentType.LAB_REPORT, DocumentType.PRESCRIPTION, DocumentType.OTHER
TODAY = date(2026, 11, 9)


def collect(**kw):
    args = {"doc_type": LAB, "record": None, "parsed": True, "findings": []}
    return signals.collect(**(args | kw))


def ids(hits) -> list[str]:
    return [h.signal for h in hits]


def stamped(record):
    stamp(record, Provenance(run_id="run", node="parse"))
    return record


def finding(validator, severity, item_id=None, field=None):
    return Finding(validator=validator, severity=severity, message="m",
                   item_id=item_id, field=field)  # fmt: skip


# -------------------------------------------------------------------
# The catalogue
# -------------------------------------------------------------------
def test_the_catalogue_is_the_list_of_section_8_8():
    routing = {f"LC-{n:02}" for n in range(1, 16)}
    info = {"LC-I1", "LC-I2", "LC-I3"}
    assert {s for s, v in SIGNALS.items() if v.kind is Kind.ROUTING} == routing
    assert {s for s, v in SIGNALS.items() if v.kind is Kind.INFO} == info
    assert {s for s, v in SIGNALS.items() if v.kind is Kind.OPERATIONAL} == {
        "OP-1", "OP-2", "OP-3"
    }  # fmt: skip
    assert {s for s, v in SIGNALS.items() if v.kind is Kind.MODEL_JUDGED} == {
        "MJ-1", "MJ-2"
    }  # fmt: skip
    assert all(v.reason.strip() and v.id == s for s, v in SIGNALS.items())


@pytest.mark.parametrize("signal", ["LC-I1", "LC-I2", "LC-I3", "OP-1", "OP-2",
                                    "OP-3", "MJ-1", "MJ-2"])  # fmt: skip
def test_info_operational_and_model_judged_signals_never_route(signal):
    hit = SignalHit(signal=signal)
    assert not hit.routes
    assert not signals.needs_review([hit])


def test_a_reason_names_its_signal_and_field():
    hit = SignalHit(signal="LC-04", item_id="obs-1", field="value_text")
    assert hit.reason == f"LC-04 [value_text]: {SIGNALS['LC-04'].reason}"
    assert SignalHit(signal="LC-12").reason == f"LC-12: {SIGNALS['LC-12'].reason}"


# -------------------------------------------------------------------
# Findings
# -------------------------------------------------------------------
V, S = Validator, Severity


@pytest.mark.parametrize(
    "found, signal, item",
    [
        (finding(V.SOURCE_TEXT, S.ERROR, "obs-1", "value_text"), "LC-04", "obs-1"),
        (finding(V.SOURCE_TEXT, S.ERROR, "document", "facility"), "LC-04", None),
        (finding(V.TEXT_LAYER, S.FLAG, "obs-2", "unit"), "LC-03", "obs-2"),
        (finding(V.UNITS_RANGES, S.FLAG, "obs-1", "unit"), "LC-09", "obs-1"),
        (finding(V.UNITS_RANGES, S.INFO, "obs-1", "unit"), "LC-I3", "obs-1"),
        (finding(V.FLAG_AGREEMENT, S.FLAG, "obs-3", "flag_printed"), "LC-10", "obs-3"),
        (finding(V.FLAG_AGREEMENT, S.INFO, "obs-3", "flag_printed"), "LC-I3", "obs-3"),
        (finding(V.REMARK_PLACEMENT, S.ERROR, "rem-1", "scope"), "LC-07", "rem-1"),
        (finding(V.MEDICINE_COVERAGE, S.ERROR, "med-1", "citation"), "LC-05", "med-1"),
        (finding(V.MEDICINE_COVERAGE, S.ERROR), "LC-06", None),
        (finding(V.DATES, S.FLAG, "document", "report_date"), "LC-11", None),
        (finding(V.DATES, S.FLAG, "patient", "date_of_birth"), "LC-11", "patient"),
        (finding(V.COVERAGE, S.INFO), "LC-I2", None),
        (finding(V.PATIENT_MATCH, S.FLAG, "patient", "name"), "LC-13", None),
    ],
    ids=[
        "LC-04",
        "LC-04-header",
        "LC-03",
        "LC-09",
        "LC-I3-range",
        "LC-10",
        "LC-I3-flag",
        "LC-07",
        "LC-05",
        "LC-06",
        "LC-11",
        "LC-11-patient",
        "LC-I2",
        "LC-13",
    ],  # fmt: skip
)
def test_each_finding_raises_its_signal_at_its_level(found, signal, item):
    [hit] = signals.from_findings([found])
    assert (hit.signal, hit.item_id, hit.field, hit.finding) == (
        signal, item, found.field, 0
    )  # fmt: skip


def test_hits_point_at_their_findings():
    found = [finding(V.COVERAGE, S.INFO), finding(V.SOURCE_TEXT, S.ERROR, "obs-1")]
    assert [h.finding for h in signals.from_findings(found)] == [0, 1]


# -------------------------------------------------------------------
# Signals read from the run
# -------------------------------------------------------------------
def test_lc01_an_item_citing_an_unreadable_line():
    record = keyed_record("d09")
    o = record.observations[0]
    line = o.printed.citation.first_line
    page = record.pages[o.printed.citation.page - 1]
    page.lines[line - 1] += " [unreadable]"

    hits = [h for h in collect(record=record) if h.signal == "LC-01"]

    assert [(h.item_id, h.field) for h in hits] == [(o.id, None)]


def test_lc01_a_header_value_on_an_unreadable_line_is_on_the_document():
    record = keyed_record("d09")
    c = record.document.printed.facility.citation
    record.pages[c.page - 1].lines[c.first_line - 1] = "[unreadable]"

    hits = [h for h in collect(record=record) if h.signal == "LC-01"]

    assert [(h.item_id, h.field) for h in hits] == [(None, "facility")]


def test_lc02_each_capped_page():
    hits = [h for h in collect(capped_pages=[1, 3]) if h.signal == "LC-02"]
    assert [h.details for h in hits] == [{"page": 1}, {"page": 3}]


@pytest.mark.parametrize("doc_type", [LAB, RX])
def test_lc08_a_parsed_type_without_a_parse(doc_type):
    assert "LC-08" in ids(collect(doc_type=doc_type, parsed=False))
    assert "LC-08" not in ids(collect(doc_type=doc_type, parsed=True))


def test_lc12_an_undated_parsed_document():
    record = keyed_record("d09")
    record.document.derived = DocumentDates()
    assert "LC-12" in ids(collect(record=record))
    assert "LC-12" not in ids(collect(record=keyed_record("d09")))


def test_lc12_does_not_repeat_lc08_for_a_record_without_a_parse():
    record = keyed_record("d17")  # pages only, undated
    assert ids(collect(doc_type=LAB, record=record, parsed=False)).count("LC-12") == 0


def test_lc14_type_other():
    hits = ids(collect(doc_type=OTHER, record=keyed_record("d17"), parsed=False))
    assert "LC-14" in hits and "LC-08" not in hits


def test_lc15_the_classifier_disagreeing():
    assert "LC-15" in ids(collect(classify_disagree=True))
    assert "LC-15" not in ids(collect())


def test_lc_i1_no_text_layer():
    assert not DOCS["d03"]["has_text_layer"]
    assert "LC-I1" in ids(collect(record=keyed_record("d03")))
    assert "LC-I1" not in ids(collect(record=keyed_record("d09")))


def test_op1_a_record_from_the_fallback_model():
    record = keyed_record("d09")
    assert "OP-1" in ids(collect(record=record, record_model_role="fallback"))
    assert "OP-1" not in ids(collect(record=record, record_model_role="primary"))


def test_op2_the_number_of_repairs():
    [hit] = [h for h in collect(repairs=2) if h.signal == "OP-2"]
    assert hit.details == {"repairs": 2}
    assert "OP-2" not in ids(collect(repairs=0))


@pytest.mark.parametrize("doc_id", PARSED_IDS)
def test_a_dataset_key_routes_only_where_the_dataset_says(doc_id):
    record = keyed_record(doc_id)
    findings = validate(record, expected_patient=expected_patient(doc_id), today=TODAY)

    hits = collect(doc_type=record.document.type, record=record, findings=findings)

    routing = sorted({h.signal for h in hits if h.routes})
    cases = DOCS[doc_id]["cases"]
    expected = (["LC-13"] if doc_id == "d16" else []) + (
        ["LC-12"] if "undated" in cases else []
    )
    assert routing == sorted(expected)


# -------------------------------------------------------------------
# Marking the record
# -------------------------------------------------------------------
def test_routing_hits_mark_their_items_and_the_rest_are_validated():
    record = stamped(keyed_record("d09"))
    first, second = record.observations[0], record.observations[1]
    hits = [
        SignalHit(signal="LC-04", item_id=first.id, field="value_text"),
        SignalHit(signal="LC-10", item_id=first.id, field="flag_printed"),
        SignalHit(signal="LC-I3", item_id=second.id, field="unit"),  # info
    ]

    signals.apply(record, hits)

    assert first.provenance.status is ItemStatus.LOW_CONFIDENCE
    assert first.provenance.low_confidence_reasons == [hits[0].reason, hits[1].reason]
    assert second.provenance.status is ItemStatus.VALIDATED
    assert second.provenance.low_confidence_reasons == []
    assert record.document.provenance.status is ItemStatus.VALIDATED
    assert record.patient.provenance.status is ItemStatus.VALIDATED
    assert all(
        i.provenance.status is ItemStatus.VALIDATED
        for i in record.items()
        if i is not first
    )


def test_document_and_patient_hits_go_on_their_own_provenance():
    record = stamped(keyed_record("d09"))
    hits = [
        SignalHit(signal="LC-12"),
        SignalHit(signal="LC-11", item_id="patient", field="date_of_birth"),
        SignalHit(signal="LC-04", item_id="no-such-item"),
    ]

    signals.apply(record, hits)

    assert record.document.provenance.low_confidence_reasons == [
        hits[0].reason, hits[2].reason
    ]  # fmt: skip
    assert record.patient.provenance.low_confidence_reasons == [hits[1].reason]
    assert record.patient.provenance.status is ItemStatus.LOW_CONFIDENCE


def test_the_same_reason_is_kept_once():
    record = stamped(keyed_record("d09"))
    hit = SignalHit(signal="LC-02", details={"page": 1})

    signals.apply(record, [hit, hit.model_copy(update={"details": {"page": 2}})])

    assert record.document.provenance.low_confidence_reasons == [hit.reason]


def test_apply_needs_a_stamped_record():
    with pytest.raises(ValueError, match="stamp"):
        signals.apply(keyed_record("d09"), [])


def test_a_marked_record_still_validates():
    record = stamped(keyed_record("d09"))
    signals.apply(record, [SignalHit(signal="LC-04", item_id="obs-1")])
    type(record).model_validate(record.model_dump())
