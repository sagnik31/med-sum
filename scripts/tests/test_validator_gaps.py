"""
Small synthetic documents for holes found in a code review: each wrong
reading must be caught, and the right one beside it must pass.
"""

from datetime import date

import pytest

from derive import build_record
from derive.numbers import parse_range
from pipeline import signals
from record.document import PageTranscription
from record.printed import DocumentType, LabReportParse, PrescriptionParse
from validators import ExpectedPatient, validate

TODAY = date(2026, 11, 24)
WHO, WHEN = "Patient: Avery Reed", "Dated: 03/03/2025"
HEAD, RULE = "| Test | Result | Unit | Range |", "| --- | --- | --- | --- |"


def cite(lines, first, last=None, quote=None):
    last = last or first
    quote = quote or "\n".join(lines[first - 1 : last])
    return {"page": 1, "first_line": first, "last_line": last, "quote": quote}


def said(lines, text, n, quote=None):
    return {"citation": cite(lines, n, quote=quote), "text": text}


def result(lines, name, value, n, last=None, **fields):
    empty = {"panel": None, "unit": None, "reference_ranges": [], "flag_printed": None}
    return {
        "citation": cite(lines, n, last),
        "test_name": name,
        "value_text": value,
        **empty,
        **fields,
    }


def medicine(lines, name, dose, first, last=None):
    empty = dict.fromkeys(
        ("strength", "frequency", "route", "duration", "instructions")
    )
    return {
        "citation": cite(lines, first, last),
        "form": "Tab",
        "name": name,
        "dose": dose,
        **empty,
    }


def check(lines, items, *, rx=False, layer=None, patient=None, dates=None, mrn=None):
    """The record and the routing signals of a one-page document."""
    nothing = {"remarks": [], "conditions": [], "vitals": [], "allergies": []}
    field = "prescription_date" if rx else "report_date"
    details = dict.fromkeys(("report_date", "collection_date", "prescription_date"))
    details |= dates if dates is not None else {field: said(lines, "03/03/2025", 2)}
    parse = (PrescriptionParse if rx else LabReportParse).model_validate(
        {
            "patient": dict.fromkeys(
                ("name", "age", "sex", "date_of_birth", "patient_id")
            )
            | {"name": said(lines, "Avery Reed", 1)}
            | (patient or {}),
            "details": {"facility": None, "clinicians": [], **details},
            "medicines" if rx else "results": items,
            **nothing,
        }
    )
    doc_type = DocumentType.PRESCRIPTION if rx else DocumentType.LAB_REPORT
    record = build_record(
        document_id="doc",
        doc_type=doc_type,
        pages=[PageTranscription(page=1, lines=lines, text_layer=layer)],
        has_text_layer=layer is not None,
        parse=parse,
    )
    findings = validate(
        record,
        expected_patient=ExpectedPatient(name="Avery Reed", mrn=mrn),
        today=TODAY,
    )
    hits = signals.from_findings(findings)
    return record, {h.signal for h in hits if h.routes}, findings


def routed(*args, **kw):
    return check(*args, **kw)[1]


# -------------------------------------------------------------------
# Values
# -------------------------------------------------------------------
@pytest.mark.parametrize("op", ["<=", ">=", "<", "≥", "</=", "=<", "< ="])
def test_a_value_that_lost_its_comparator_is_an_error(op):
    lines = [WHO, WHEN, HEAD, RULE, f"| Marker | {op} 5 | U/L | |"]
    assert routed(lines, [result(lines, "Marker", "5", 5, unit="U/L")]) == {"LC-04"}
    assert not routed(lines, [result(lines, "Marker", f"{op} 5", 5, unit="U/L")])


@pytest.mark.parametrize(
    "printed, ok",
    [
        ("Marker 4.2 U/L", True),
        ("Marker\n4.2\n1.0 - 6.0", True),  # cells, each on a line of its own
        ("Marker4.2U/L", True),
        ("Marker 14.2 U/L", False),
        ("Marker 4.25 U/L", False),
        ("Marker 1,4.2 U/L", False),
        ("Marker <= 4.2 U/L", False),
    ],
)
def test_the_text_layer_confirms_a_whole_number_only(printed, ok):
    lines = [WHO, WHEN, HEAD, RULE, "| Marker | 4.2 | | 1.0 - 6.0 |"]
    layer = "\n".join([WHO, WHEN, printed])
    found = routed(lines, [result(lines, "Marker", "4.2", 5)], layer=layer)
    assert ("LC-03" in found) is not ok


@pytest.mark.parametrize("unit", ["10^3/uL", "10³/µL", "x10^3/uL", "/cumm"])
def test_a_range_s_unit_may_start_with_a_number(unit):
    r = parse_range(f"4 - 11 {unit}")
    assert (r.low, r.high, r.unit) == (4, 11, unit)
    assert parse_range(f"< 11 {unit}").high == 11
    lines = [WHO, WHEN, HEAD, RULE, f"| WBC | 12.5 | {unit} | 4 - 11 {unit} |"]
    item = result(
        lines, "WBC", "12.5", 5, unit=unit, reference_ranges=[f"4 - 11 {unit}"]
    )
    record, found, _ = check(lines, [item])
    assert record.observations[0].derived.flag_computed.value == "high"


def test_a_range_followed_by_an_unknown_number_is_not_parsed():
    assert not parse_range("4 - 11 10 - 12").parsed


# -------------------------------------------------------------------
# Rows
# -------------------------------------------------------------------
RX = [WHO, WHEN, "Tab Synalpha 1 tab", "after food", "Tab Synbeta 2 tabs"]


def test_medicines_citing_each_other_s_lines_can_t_trade_doses():
    items = [
        medicine(RX, "Synalpha", "2 tabs", 3, 5),
        medicine(RX, "Synbeta", "1 tab", 3, 5),
    ]
    assert "LC-05" in routed(RX, items, rx=True)


def test_a_medicine_can_t_hide_another_s_line():
    found = routed(RX, [medicine(RX, "Synalpha", "1 tab", 3, 5)], rx=True)
    assert {"LC-05", "LC-06"} <= found


def test_a_medicine_may_cite_the_lines_that_continue_it():
    items = [
        medicine(RX, "Synalpha", "1 tab", 3, 4),
        medicine(RX, "Synbeta", "2 tabs", 5),
    ]
    assert not routed(RX, items, rx=True)


def test_a_result_is_not_read_from_the_header_its_citation_takes_in():
    lines = [WHO, WHEN, HEAD, RULE, "| Marker | 5 | U/L | |"]
    assert "LC-04" in routed(lines, [result(lines, "Test", "Result", 3, 5)])
    assert not routed(lines, [result(lines, "Marker", "5", 3, 5, unit="U/L")])


PANELS = [WHO, WHEN, "## Serum", HEAD, RULE, "| Creatinine | 1.0 | mg/dL | |",
          "## Urine", HEAD, RULE, "| Creatinine | 1.6 | mg/dL | |"]  # fmt: skip


def creatinines(lines, serum, urine, at=10):
    return [
        result(lines, "Creatinine", "1.0", 6, unit="mg/dL", panel=serum),
        result(lines, "Creatinine", "1.6", at, unit="mg/dL", panel=urine),
    ]


def test_a_result_s_panel_is_the_heading_it_is_under():
    assert not routed(PANELS, creatinines(PANELS, "Serum", "Urine"))
    assert routed(PANELS, creatinines(PANELS, "Serum", "Serum")) == {"LC-04"}


@pytest.mark.parametrize("heading", ["**Urine**", "| URINE | | | |"])
def test_a_section_may_be_headed_in_bold_or_by_a_row(heading):
    lines = [*PANELS]
    lines[2], lines[6] = (
        heading.replace("Urine", "Serum").replace("URINE", "SERUM"),
        heading,
    )
    serum = "SERUM" if heading.startswith("|") else "Serum"
    items = creatinines(lines, serum, serum)
    assert "LC-04" in routed(lines, items)


@pytest.mark.parametrize(
    "between", ["### Part two", "## Serum (contd.)", "# Synthetic Lab", "## Remarks"]
)
def test_a_heading_that_does_not_end_the_panel(between):
    lines = ["# Synthetic Lab", WHEN, *PANELS[2:]]
    lines[6] = between
    patient = {"name": None}
    items = creatinines(lines, "Serum", "Serum")
    found = routed(lines, items, patient=patient)
    assert "LC-04" not in found


# -------------------------------------------------------------------
# The patient and the date
# -------------------------------------------------------------------
@pytest.mark.parametrize("quote", [None, "SYN-OTHER"])
def test_the_hospital_id_label_is_read_on_the_line_not_the_quote(quote):
    lines = [WHO, WHEN, "MRN: SYN-OTHER"]
    patient = {"patient_id": said(lines, "SYN-OTHER", 3, quote)}
    assert "LC-13" in routed(lines, [], patient=patient, mrn="SYN-EXPECTED")
    assert not routed(lines, [], patient=patient, mrn="SYN-OTHER")


def test_a_lab_number_beside_the_hospital_id_is_not_taken_for_it():
    lines = [WHO, WHEN, "UHID: SYN-1 | Lab No: L-77"]
    patient = {"patient_id": said(lines, "L-77", 3, "L-77")}
    assert not routed(lines, [], patient=patient, mrn="SYN-1")
    assert "LC-13" in routed(lines, [], patient=patient, mrn="SYN-2")


def test_a_prescription_is_dated_by_its_own_date():
    lines = [WHO, "Date: 12/04/2025", "Prior report: 11/04/2025", "Tab Synalpha 1 tab"]
    items = [medicine(lines, "Synalpha", "1 tab", 4)]
    both = {
        "prescription_date": said(lines, "12/04/2025", 2),
        "report_date": said(lines, "11/04/2025", 3),
    }
    record, found, _ = check(lines, items, rx=True, dates=both)
    assert record.document.derived.document_date == date(2025, 4, 12)
    assert not found
    other_only = {"report_date": both["report_date"]}
    assert "LC-11" in routed(lines, items, rx=True, dates=other_only)


# -------------------------------------------------------------------
# A citation's size
# -------------------------------------------------------------------
def test_a_citation_past_the_page_costs_no_more_than_the_page():
    lines = [WHO, WHEN, HEAD, RULE, "| Marker | 5 | U/L | |"]
    huge = result(lines, "Marker", "5", 5, flag_printed=None)
    huge["citation"]["last_line"] = 10**12
    _, found, findings = check(lines, [huge])
    assert "LC-04" in found
    assert all(len(f.lines) <= 51 for f in findings)
    _, found, _ = check(RX, [medicine(RX, "Synalpha", "1 tab", 3, 10**12)], rx=True)
    assert "LC-04" in found


@pytest.mark.parametrize(
    "header, row, routes",
    [
        (HEAD, "| Marker | 5 | U/L | |", True),
        ("| Test | Result | Unit(s) | Range |", "| Marker | 5 | % | |", True),
        (HEAD, "| Marker | 5 | | |", False),
        (HEAD, "| Marker | 5 | - | |", False),
        (HEAD, "| Marker | 5 | N/A | |", False),
        (HEAD, "| Marker | 5 | U/L |", False),  # its cells can't be told apart
        ("| Test | Result | Method | Range |", "| Marker | 5 | ELISA | |", False),
    ],
)
def test_a_printed_unit_left_out_of_the_record_goes_to_review(header, row, routes):
    lines = [WHO, WHEN, header, RULE, row]
    found = routed(lines, [result(lines, "Marker", "5", 5)])
    assert ("LC-23" in found) is routes


def test_a_unit_read_with_its_value_is_not_left_out():
    lines = [WHO, WHEN, HEAD, RULE, "| Marker | 5 U/L | U/L | |"]
    found = routed(lines, [result(lines, "Marker", "5 U/L", 5)])
    assert "LC-23" not in found
    plain = [WHO, WHEN, HEAD, RULE, "| Marker | 5 | U/L | |"]
    assert not routed(plain, [result(plain, "Marker", "5", 5, unit="U/L")])
