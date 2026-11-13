"""
The validators of section 8.3. Every clean answer key must pass (no false
alarms); a targeted change to a key must fail the validator meant to catch
it, and only that one where it matters. No model is called: the keys stand
in for the parse output, and the expected transcriptions for the
transcription step.
"""

from datetime import date

import pytest

from dataset_records import DOC_IDS, expected_patient, keyed_record, parse_of
from derive import build_record
from record.common import PrintedText
from record.document import ComputedFlag, DocumentRecord, PageTranscription
from record.printed import PrintedMedicine, RemarkScope
from validators import ExpectedPatient, Finding, Severity, Validator, validate
from validators.layout import (
    check_coverage,
    check_medicine_coverage,
    check_result_coverage,
    result_rows,
    check_remark_placement,
    medicine_lines,
    unused_lines,
)
from validators.patient import check_patient_match, names_match, read_age, read_sex
from validators.source import check_source_text, check_text_layer
from validators.text import contains
from validators.values import (
    check_dates,
    check_flag_agreement,
    check_range_bounds,
    check_units_ranges,
    read_printed_flag,
)

TODAY = date(2026, 11, 8)


def rebuilt(record: DocumentRecord) -> DocumentRecord:
    """The record built again from its (changed) printed parts, as the pipeline would."""
    return build_record(
        document_id=record.document.document_id,
        doc_type=record.document.type,
        pages=record.pages,
        has_text_layer=record.document.has_text_layer,
        parse=parse_of(record),
    )


def obs(record: DocumentRecord, test_name: str):
    return next(o for o in record.observations if o.printed.test_name == test_name)


def remark(record: DocumentRecord, starts: str):
    return next(r for r in record.remarks if r.printed.text.startswith(starts))


def problems(findings: list[Finding]) -> set[tuple]:
    """(validator, item, field) of every error and flag."""
    return {
        (f.validator.value, f.item_id, f.field)
        for f in findings
        if f.severity is not Severity.INFO
    }


def set_line(record: DocumentRecord, page: int, line: int, text: str) -> None:
    """Change a transcription line, and every citation quote of that one line."""
    record.pages[page - 1].lines[line - 1] = text
    for item in record.items():
        c = item.printed.citation
        if (c.page, c.first_line, c.last_line) == (page, line, line):
            c.quote = text


# -------------------------------------------------------------------
# Clean answer keys pass
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_clean_answer_keys_raise_no_errors_or_flags(doc_id):
    findings = validate(
        keyed_record(doc_id), expected_patient=expected_patient(doc_id), today=TODAY
    )

    expected = set()
    if doc_id == "d16":  # printed for Arjun Mehta, 41; uploaded to Arjun Menon
        expected = {
            ("patient_match", "patient", "name"),
            ("patient_match", "patient", "age"),
        }
    assert problems(findings) == expected
    assert {f.validator for f in findings} <= {
        Validator.COVERAGE,
        Validator.PATIENT_MATCH,
    }


def test_text_layer_documents_are_really_checked():
    record = keyed_record("d01")

    assert record.document.has_text_layer
    assert all(p.text_layer and p.text_layer.strip() for p in record.pages)


def test_a_document_of_type_other_has_nothing_to_validate():
    assert validate(keyed_record("d17"), expected_patient=expected_patient("d17")) == []


def test_findings_can_be_stored_as_json():
    finding = validate(keyed_record("d16"), expected_patient=expected_patient("d16"))[
        -1
    ]

    assert Finding.model_validate_json(finding.model_dump_json()) == finding


# -------------------------------------------------------------------
# Token matching
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "haystack, needle, found",
    [
        ("| Haemoglobin | 10.4 | L | g/dL |", "10.4", True),
        ("| Haemoglobin | 10.4 | L | g/dL |", "10", False),
        ("| Haemoglobin | 10.4 | L | g/dL |", "0.4", False),
        ("| Haemoglobin | 10.4 | L | g/dL |", "L", True),
        ("| Haemoglobin | 10.4 | g/dL |", "L", False),
        ("| Platelets | 2,45,000 |", "45,000", False),
        ("| Platelets | 2,45,000 |", "2,45,000", True),
        ("| CRP | < 0.5 | mg/L |", "< 0.5", True),
        ("| CRP | <  0.5 | mg/L |", "< 0.5", True),  # spacing ignored
        ("| TLC | 6.8 | 10³/µL |", "10³/µL", True),
        ("| TLC | 6.8 | 103/μL |", "10³/µL", True),  # NFKC forms
        ("Ref. By: Dr. Anil Deshpande", "Dr. Anil Deshpande", True),
        ("Vitals: BP 142/88 mmHg, Pulse 82 /min", "82", True),
        ("Vitals: BP 142/88 mmHg", "42/88", False),
        ("haemoglobin", "Haemoglobin", False),  # verbatim: case counts
    ],
)  # fmt: skip
def test_contains_matches_whole_tokens(haystack, needle, found):
    assert contains(haystack, needle) is found


# -------------------------------------------------------------------
# 1: values on their cited lines
# -------------------------------------------------------------------
def test_a_changed_value_is_not_on_its_line():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.value_text = "10.5"

    assert problems(check_source_text(record)) == {
        ("source_text", "obs-1", "value_text")
    }


def test_part_of_a_value_is_not_the_value():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.value_text = "10"

    assert problems(check_source_text(record)) == {
        ("source_text", "obs-1", "value_text")
    }


def test_a_quote_that_differs_from_its_lines():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.citation.quote = "| Haemoglobin | 10.5 |"

    assert problems(check_source_text(record)) == {("source_text", "obs-1", None)}


@pytest.mark.parametrize(
    "part",
    ["| Haemoglobin | 10.4 |", "Haemoglobin | 10.4", "Haemoglobin"],
    ids=["leading-cells", "without-outer-bars", "one-cell"],
)
def test_an_exact_part_of_the_cited_line_is_a_faithful_quote(part):
    record = keyed_record("d01")
    c = obs(record, "Haemoglobin").printed.citation
    assert part in c.quote
    c.quote = part

    assert check_source_text(record) == []


@pytest.mark.parametrize(
    "part", ["Haemoglobin | 10", "aemoglobin"], ids=["cut-number", "cut-word"]
)
def test_a_part_that_cuts_a_token_is_not_a_quote(part):
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.citation.quote = part

    assert problems(check_source_text(record)) == {("source_text", "obs-1", None)}


def test_a_quote_from_another_line_is_not_on_the_cited_line():
    record = keyed_record("d01")
    hb, rbc = obs(record, "Haemoglobin"), obs(record, "RBC Count")
    hb.printed.citation.quote = rbc.printed.citation.quote

    assert problems(check_source_text(record)) == {("source_text", "obs-1", None)}


def test_a_label_and_value_quoted_from_a_header_line():
    record = keyed_record("d01")
    name = record.patient.printed.name
    line = record.pages[0].lines[name.citation.first_line - 1]
    assert ":" in line and name.text in line
    name.citation.quote = name.text

    assert check_source_text(record) == []


def test_a_quote_differing_only_in_spacing_is_the_same_quote():
    record = keyed_record("d01")
    c = obs(record, "Haemoglobin").printed.citation
    c.quote = c.quote.replace(" | ", "  |  ")

    assert check_source_text(record) == []


def test_a_citation_past_the_end_of_the_page_is_one_error():
    record = keyed_record("d01")
    c = obs(record, "Haemoglobin").printed.citation
    c.first_line = c.last_line = 999

    findings = check_source_text(record)
    assert len(findings) == 1 and "not in the transcription" in findings[0].message


def test_a_citation_to_the_wrong_row():
    record = keyed_record("d01")
    hb, rbc = obs(record, "Haemoglobin"), obs(record, "RBC Count")
    hb.printed.citation = rbc.printed.citation.model_copy()

    found = problems(check_source_text(record))
    assert {("source_text", "obs-1", "test_name"), ("source_text", "obs-1", "value_text"),
            ("source_text", "obs-1", "unit")} <= found  # fmt: skip


def test_a_panel_must_be_a_heading_above_its_row():
    record = keyed_record("d01")
    # LIPID PROFILE is printed on page 2, after the CBC on page 1.
    obs(record, "Haemoglobin").printed.panel = "LIPID PROFILE"

    assert problems(check_source_text(record)) == {("source_text", "obs-1", "panel")}


def test_a_panel_on_an_earlier_page_is_above():
    record = keyed_record("d01")
    obs(record, "HbA1c").printed.panel = "COMPLETE BLOOD COUNT"

    assert check_source_text(record) == []


def test_header_values_are_checked_too():
    record = keyed_record("d01")
    record.patient.printed.name.text = "Mrs. Meera Iyengar"
    record.document.printed.report_date.text = "15/06/2024 02:45 PM"

    assert problems(check_source_text(record)) == {
        ("source_text", "patient", "name"),
        ("source_text", "document", "report_date"),
    }


def test_medicines_vitals_allergies_and_conditions_are_checked():
    record = keyed_record("d02")
    record.medications[0].printed.strength = "1000 mg"
    record.vitals[0].printed.value_text = "140/88"
    record.allergies[0].printed.text = "Penicillin"
    record.conditions[0].printed.text = "Hypotension"

    assert problems(check_source_text(record)) == {
        ("source_text", "med-1", "strength"),
        ("source_text", "vit-1", "value_text"),
        ("source_text", "alg-1", "text"),
        ("source_text", "cond-1", "text"),
    }


# -------------------------------------------------------------------
# 2: the PDF text layer (decision 40)
# -------------------------------------------------------------------
def test_a_misread_shared_by_transcription_and_parse_is_caught_by_the_text_layer():
    # The vision step read 10.4 as 10.5; the parse copied it faithfully.
    record = keyed_record("d01")
    hb = obs(record, "Haemoglobin")
    c = hb.printed.citation
    set_line(record, c.page, c.first_line, c.quote.replace("10.4", "10.5"))
    hb.printed.value_text = "10.5"

    assert check_source_text(record) == []
    findings = check_text_layer(record)
    assert problems(findings) == {("text_layer", "obs-1", "value_text")}
    assert findings[0].severity is Severity.FLAG
    assert "10.5" in findings[0].details["transcription"]


def test_no_text_layer_check_without_a_text_layer():
    record = keyed_record("d03")  # an image-only PDF
    obs(record, "Haemoglobin").printed.value_text = "99.9"

    assert record.pages[0].text_layer is None
    assert check_text_layer(record) == []


def test_the_text_layer_is_compared_with_ligatures_expanded_and_spacing_ignored():
    record = keyed_record("d01")
    record.pages[0].text_layer = (
        record.pages[0]
        .text_layer.replace("Haemoglobin", "Haemo\nglobin")
        .replace("fi", "ﬁ")
    )

    assert check_text_layer(record) == []


def misread(record: DocumentRecord, test_name: str, old: str, new: str):
    """The vision step read `old` as `new` in a result cell; the parse copied it."""
    o = obs(record, test_name)
    c = o.printed.citation
    set_line(record, c.page, c.first_line, c.quote.replace(f"| {old} |", f"| {new} |"))
    o.printed.value_text = new
    return o


def test_a_misread_printed_in_another_row_is_caught_per_row():
    # "1.7" is on the page, inside the next row's "mL/min/1.73m²", so a
    # page-wide search found it; it is not in the creatinine row.
    record = keyed_record("d06")
    creatinine = misread(record, "Serum Creatinine", "1.6", "1.7")

    assert check_source_text(record) == []
    findings = check_text_layer(record)
    assert problems(findings) == {("text_layer", creatinine.id, "value_text")}
    assert "its row" in findings[0].message


def test_each_field_of_a_row_is_checked_in_that_row():
    # A unit printed on the page, but in other rows, and a flag that isn't
    # printed. (A one-letter flag would be found inside "mg/dL": the text
    # layer is compared without token edges, so short values are weak here.)
    record = keyed_record("d06")
    urea = obs(record, "Blood Urea")
    c = urea.printed.citation
    set_line(
        record, c.page, c.first_line, "| Blood Urea | 46 | Low | mmol/L | 15 - 40 |"
    )
    urea.printed.flag_printed = "Low"
    urea.printed.unit = "mmol/L"

    assert check_source_text(record) == []
    assert problems(check_text_layer(record)) == {
        ("text_layer", urea.id, "flag_printed"),
        ("text_layer", urea.id, "unit"),
    }


def test_a_row_is_found_wherever_its_name_is_printed():
    # The text layer prints the name twice; the value is in the second row.
    record = keyed_record("d06")
    # The text layer names the test first in a note at the top of the page,
    # far from its row; the row is found at its second mention.
    record.pages[0].text_layer = (
        "Blood Urea repeated on request" + chr(10) + (record.pages[0].text_layer)
    )

    assert check_text_layer(record) == []


def test_a_name_missing_from_the_text_layer_is_flagged_and_its_row_checked_on_the_page():
    record = keyed_record("d06")
    urea = obs(record, "Blood Urea")
    c = urea.printed.citation
    set_line(record, c.page, c.first_line, c.quote.replace("Blood Urea", "Blood Uria"))
    urea.printed.test_name = "Blood Uria"

    assert problems(check_text_layer(record)) == {("text_layer", urea.id, "test_name")}


def test_a_row_keeps_working_when_the_text_layer_splits_and_spaces_it():
    record = keyed_record("d06")
    nl = chr(10)
    layer = record.pages[0].text_layer
    record.pages[0].text_layer = layer.replace(nl, f" {nl} ")

    assert check_text_layer(record) == []


@pytest.mark.parametrize(
    "doc_id", [d for d in DOC_IDS if keyed_record(d).document.has_text_layer]
)
def test_every_clean_text_layer_document_passes_per_row(doc_id):
    assert check_text_layer(keyed_record(doc_id)) == []


# -------------------------------------------------------------------
# 3b: a value read from its own range
# -------------------------------------------------------------------
def test_a_value_taken_from_its_range_is_flagged():
    record = keyed_record("d06")
    urea = obs(record, "Blood Urea")  # | Blood Urea | 46 | H | mg/dL | 15 - 40 |
    urea.printed.value_text = "15"

    findings = check_range_bounds(record)
    assert problems(findings) == {("range_bound", urea.id, "value_text")}
    assert findings[0].severity is Severity.FLAG
    # Validators 1 and 2 can't see it: "15" is on the line and in the text layer.
    assert check_source_text(record) == [] and check_text_layer(record) == []


@pytest.mark.parametrize("bound", ["15", "40"])
def test_a_value_really_equal_to_a_bound_is_printed_in_its_own_cell(bound):
    record = keyed_record("d06")
    urea = misread(record, "Blood Urea", "46", bound)

    assert check_range_bounds(record) == []
    assert urea.printed.value_text == bound


@pytest.mark.parametrize(
    "value, ranges, row, flagged",
    [
        ("150", ["< 150"], "| Triglycerides | 210 | H | mg/dL | < 150 |", True),
        ("1,50,000", ["1,50,000 - 4,50,000"],
         "| Platelets | 2,45,000 | | /cumm | 1,50,000 - 4,50,000 |", True),
        ("4.5", ["Up to 4.5"], "| TSH | 6.1 | H | µIU/mL | Up to 4.5 |", True),
        # The bound is printed twice in the range only.
        ("100", ["70 - 100", "Fasting: < 100"],
         "| Glucose | 92 | | mg/dL | 70 - 100; Fasting: < 100 |", True),
        # Not a bound: some other number of the row.
        ("210", ["< 150"], "| Triglycerides | 210 | H | mg/dL | < 150 |", False),
        # No number to compare.
        ("Positive", ["Negative"], "| HBsAg | Positive | | | Negative |", False),
        # Printed in its own cell as well.
        ("150", ["< 150"], "| Triglycerides | 150 | | mg/dL | < 150 |", False),
    ],
)  # fmt: skip
def test_range_bounds(value, ranges, row, flagged):
    record = keyed_record("d06")
    urea = obs(record, "Blood Urea")
    c = urea.printed.citation
    set_line(record, c.page, c.first_line, row)
    urea.printed.value_text = value
    urea.printed.reference_ranges = ranges
    urea.printed.test_name = row.split("|")[1].strip()

    assert bool(check_range_bounds(record)) is flagged


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_no_clean_key_has_a_value_read_from_its_range(doc_id):
    assert check_range_bounds(keyed_record(doc_id)) == []


# -------------------------------------------------------------------
# 3: units and ranges
# -------------------------------------------------------------------
def test_a_range_with_its_bounds_reversed():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.reference_ranges = ["15.0 - 12.0"]

    assert problems(check_units_ranges(record)) == {
        ("units_ranges", "obs-1", "reference_ranges[0]")
    }


def test_a_range_in_another_unit_than_the_value():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.reference_ranges = ["120 - 150 g/L"]

    findings = check_units_ranges(record)
    assert problems(findings) == {("units_ranges", "obs-1", "reference_ranges[0]")}
    # Nor is a flag computed from it.
    assert obs(rebuilt(record), "Haemoglobin").derived.flag_computed is None


def test_a_unit_that_does_not_fit_the_test_is_flagged_and_an_unknown_one_noted():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.unit = "%"
    obs(record, "PCV").printed.unit = "vol pc"

    findings = {(f.item_id, f.severity) for f in check_units_ranges(record)}
    assert findings == {("obs-1", Severity.FLAG), ("obs-3", Severity.INFO)}


def test_an_unreadable_range_with_numbers_is_noted():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.reference_ranges = ["see note 3"]

    [finding] = check_units_ranges(record)
    assert finding.severity is Severity.INFO


# -------------------------------------------------------------------
# 4: printed flag against computed flag (decision 11)
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "printed, direction",
    [("H", ComputedFlag.HIGH), ("High", ComputedFlag.HIGH), ("H*", ComputedFlag.HIGH),
     ("↑", ComputedFlag.HIGH), ("L", ComputedFlag.LOW), ("LL", ComputedFlag.LOW),
     ("Normal", ComputedFlag.NORMAL), ("*", "abnormal"), ("CRITICAL", "abnormal"),
     (None, None), ("Q", "")],
)  # fmt: skip
def test_read_printed_flag(printed, direction):
    assert read_printed_flag(printed) == direction


@pytest.mark.parametrize(
    "test, printed, flagged",
    [
        ("Haemoglobin", None, True),  # low, nothing printed
        ("Haemoglobin", "H", True),  # low, H printed
        ("Haemoglobin", "*", False),  # low, a mark without direction
        ("RBC Count", "*", True),  # normal, marked abnormal
        ("RBC Count", "N", False),
        ("RDW-CV", "H*", False),
        ("HbA1c", "L", False),  # several ranges: nothing computed, nothing compared
    ],
)
def test_flag_disagreements_are_flagged(test, printed, flagged):
    record = keyed_record("d01")
    obs(record, test).printed.flag_printed = printed

    findings = check_flag_agreement(record)
    assert bool(problems(findings)) is flagged
    assert all(f.severity is Severity.FLAG for f in findings)


def test_an_unknown_printed_flag_is_noted_not_flagged():
    record = keyed_record("d01")
    obs(record, "Haemoglobin").printed.flag_printed = "Q"

    [finding] = check_flag_agreement(record)
    assert finding.severity is Severity.INFO


# -------------------------------------------------------------------
# 5: remark placement (decision 25, section 6 bug 1)
# -------------------------------------------------------------------
def rescope(record, starts, scope, test_name=None, panel=None):
    p = remark(record, starts).printed
    p.scope, p.test_name, p.panel = RemarkScope(scope), test_name, panel
    return rebuilt(record)


def test_advice_after_a_single_test_panel_is_not_on_that_test():
    # d03: "Suggest iron studies" sits under its own heading after GLUCOSE.
    record = rescope(keyed_record("d03"), "Suggest iron", "test", "HbA1c",
                     "GLYCATED HAEMOGLOBIN")  # fmt: skip

    assert problems(check_remark_placement(record)) == {
        ("remark_placement", "rem-2", "scope")
    }


def test_advice_after_a_single_test_panel_is_not_on_that_panel():
    record = rescope(keyed_record("d03"), "Suggest iron", "panel",
                     panel="GLYCATED HAEMOGLOBIN")  # fmt: skip

    assert problems(check_remark_placement(record)) == {
        ("remark_placement", "rem-2", "scope")
    }


def test_a_panel_remark_on_a_panel_printed_after_it():
    record = rescope(keyed_record("d01"), "Microcytic", "panel", panel="LIPID PROFILE")

    assert problems(check_remark_placement(record)) == {
        ("remark_placement", "rem-1", "scope")
    }


def test_a_remark_on_a_test_or_panel_that_isnt_there():
    record = rescope(keyed_record("d01"), "Microcytic", "test", "Ferritin", "CBC")
    assert problems(check_remark_placement(record)) == {
        ("remark_placement", "rem-1", "scope")
    }

    record = rescope(keyed_record("d01"), "Microcytic", "panel", panel="IRON STUDIES")
    assert problems(check_remark_placement(record)) == {
        ("remark_placement", "rem-1", "scope")
    }


def test_a_comment_right_under_the_last_row_passes_on_test_or_panel():
    # The limit of a layout check: d01's CBC comment is directly under the
    # WBC row, so reading it as on WBC (bug 1) is the parse step's call.
    record = rescope(keyed_record("d01"), "Microcytic", "test", "Total WBC Count",
                     "COMPLETE BLOOD COUNT")  # fmt: skip

    assert check_remark_placement(record) == []


def test_document_remarks_may_be_anywhere():
    record = rescope(keyed_record("d01"), "Microcytic", "document")

    assert check_remark_placement(record) == []


# -------------------------------------------------------------------
# 6: medicines (none invented, none dropped)
# -------------------------------------------------------------------
PRESCRIPTIONS = ["d02", "d04", "d07", "d10", "d12", "d14", "d15"]


@pytest.mark.parametrize("doc_id", PRESCRIPTIONS)
def test_medicine_lines_are_exactly_the_medicine_rows(doc_id):
    record = keyed_record(doc_id)
    assert record.document.type.value == "prescription"
    rows = {(m.printed.citation.page, m.printed.citation.first_line)
            for m in record.medications}  # fmt: skip

    assert set(medicine_lines(record)) == rows


def test_lab_reports_have_no_medicine_lines():
    assert medicine_lines(keyed_record("d01")) == []


def test_medicine_lines_without_a_table():
    lines = ["# Dr. A", "Rx", "1. Tab. Dolo 650 mg 1-0-1 x 5 days", "Cap Uprise-D3 60K weekly",
             "Syp. Ascoril 5 ml TDS", "Advice: rest", "Inj. B12 once"]  # fmt: skip
    record = keyed_record("d02")
    record.pages = [PageTranscription(page=1, lines=lines)]

    assert medicine_lines(record) == [(1, 3), (1, 4), (1, 5), (1, 7)]


def test_a_dropped_medicine_is_reported_on_its_line():
    record = keyed_record("d04")
    dropped = record.medications.pop(2)

    [finding] = check_medicine_coverage(record)
    assert finding.severity is Severity.ERROR
    assert finding.lines == [dropped.printed.citation.first_line]
    assert dropped.printed.name in finding.details["line"]


def test_an_invented_medicine_is_reported():
    record = keyed_record("d02")
    vitals_line = record.vitals[0].printed.citation
    record.medications.append(record.medications[0].model_copy(update={
        "id": "med-4",
        "printed": PrintedMedicine(citation=vitals_line, form="Tab.", name="Pulse",
                                   strength=None, dose=None, frequency=None,
                                   route=None, duration=None, instructions=None)}))  # fmt: skip

    assert problems(check_medicine_coverage(record)) == {
        ("medicine_coverage", "med-4", "citation")
    }


# -------------------------------------------------------------------
# 7: dates
# -------------------------------------------------------------------
def test_an_impossible_date_is_flagged():
    record = keyed_record("d01")
    record.document.printed.report_date.text = "31/02/2024 02:45 PM"

    assert problems(check_dates(record, TODAY)) == {
        ("dates", "document", "report_date")
    }


def test_a_future_date_is_flagged():
    record = keyed_record("d02")

    assert check_dates(record, TODAY) == []
    assert problems(check_dates(record, date(2024, 6, 1))) == {
        ("dates", "document", "prescription_date")
    }


def test_collection_after_the_report_is_flagged():
    record = keyed_record("d01")
    record.document.printed.collection_date.text = "15/06/2024 08:10 AM"

    assert problems(check_dates(record, TODAY)) == {
        ("dates", "document", "collection_date")
    }


def test_a_date_of_birth_after_the_document_is_flagged():
    record = keyed_record("d01")
    cite = record.patient.printed.name.citation
    record.patient.printed.date_of_birth = PrintedText(citation=cite, text="01/01/2030")
    assert problems(check_dates(record, TODAY)) == {
        ("dates", "patient", "date_of_birth")
    }

    record.patient.printed.date_of_birth = PrintedText(citation=cite, text="12 Apr 67")
    assert problems(check_dates(record, TODAY)) == {
        ("dates", "patient", "date_of_birth")
    }


# -------------------------------------------------------------------
# 6b: no results-table row dropped
# -------------------------------------------------------------------
def dropped(record: DocumentRecord, test_name: str) -> tuple[int, int]:
    """Drop a result from the record; (page, line) of its row."""
    o = obs(record, test_name)
    record.observations.remove(o)
    return o.printed.citation.page, o.printed.citation.first_line


def test_a_dropped_lab_row_is_an_error_on_its_row():
    record = keyed_record("d06")
    page, line = dropped(record, "Serum Creatinine")

    [finding] = check_result_coverage(record)
    assert finding.validator is Validator.RESULT_COVERAGE
    assert finding.severity is Severity.ERROR
    assert (finding.item_id, finding.page, finding.lines) == (None, page, [line])
    assert "Serum Creatinine" in finding.details["line"]


def test_every_row_of_every_results_table_is_listed():
    record = keyed_record("d01")
    rows = result_rows(record)

    # One row per result, and every result cites one of them.
    assert len(rows) == len(record.observations)
    assert {(o.printed.citation.page, o.printed.citation.first_line)
            for o in record.observations} == set(rows)  # fmt: skip


@pytest.mark.parametrize(
    "header",
    ["| Test | Result | Unit |", "| Investigation | Observed Value | Units |",
     "| Parameter | Value | Range |", "| Test | Results | Flag |",
     "| Test | Reading |"],
)  # fmt: skip
def test_a_results_table_is_known_by_its_result_column(header):
    record = keyed_record("d06")
    lines = record.pages[0].lines
    first = lines.index("| Test | Result | Flag | Unit | Reference Range |")
    lines[first] = header

    assert (1, first + 3) in result_rows(record)


def test_tables_without_a_result_column_are_not_results_tables():
    # d20's patient details are a two-column key-value table.
    record = keyed_record("d20")
    details = [n for n, line in enumerate(record.pages[0].lines, start=1)
               if "Patient Name" in line]  # fmt: skip
    rows = result_rows(record)

    assert details and not any((1, n) in rows for n in details)
    assert len(rows) == len(record.observations)


def test_a_prescription_table_is_left_to_the_medicine_check():
    assert result_rows(keyed_record("d02")) == []


def test_a_heading_row_inside_a_table_is_not_a_result():
    record = keyed_record("d06")
    lines = record.pages[0].lines
    at = lines.index("| Blood Urea | 46 | H | mg/dL | 15 - 40 |")
    lines.insert(at, "| **Renal** |  |  |  |  |")
    record = rebuilt_with_lines(record, at + 1)

    assert check_result_coverage(record) == []


def test_a_table_without_a_header_row_is_not_judged():
    record = keyed_record("d06")
    lines = record.pages[0].lines
    header = lines.index("| Test | Result | Flag | Unit | Reference Range |")
    del lines[header : header + 2]  # the header and its separator
    record = rebuilt_with_lines(record, header + 1, shift=-2)
    dropped(record, "Blood Urea")

    assert check_result_coverage(record) == []


def test_results_printed_as_lines_stay_with_coverage():
    # d18 prints "Test: value unit (Ref: ...)" lines, not a table: a dropped
    # one is only listed as unused, which the fault check reports.
    record = keyed_record("d18")
    assert result_rows(record) == []
    dropped(record, record.observations[0].printed.test_name)
    assert check_result_coverage(record) == []
    assert check_coverage(record)


def test_a_result_citing_several_lines_covers_each():
    record = keyed_record("d06")
    o = obs(record, "Blood Urea")
    nxt = obs(record, "Blood Urea Nitrogen (BUN)")
    record.observations.remove(nxt)
    o.printed.citation.last_line = nxt.printed.citation.first_line
    o.printed.citation.quote = "\n".join(
        record.pages[0].lines[
            o.printed.citation.first_line - 1 : o.printed.citation.last_line
        ]
    )

    assert check_result_coverage(record) == []


def rebuilt_with_lines(record: DocumentRecord, from_line: int, shift: int = 1):
    """Move every citation at or below `from_line` of page 1 by `shift` lines."""
    for item in record.items():
        c = item.printed.citation
        if c.page == 1 and c.first_line >= from_line:
            c.first_line += shift
            c.last_line += shift
    for value in (record.patient.printed if record.patient else None,
                  record.document.printed):  # fmt: skip
        if value is None:
            continue
        for name in type(value).model_fields:
            text = getattr(value, name)
            for t in text if isinstance(text, list) else [text]:
                c = getattr(t, "citation", None)
                if c is not None and c.page == 1 and c.first_line >= from_line:
                    c.first_line += shift
                    c.last_line += shift
    return record


# -------------------------------------------------------------------
# 8: coverage (decision 39)
# -------------------------------------------------------------------
def unused_text(record):
    return [record.pages[p - 1].lines[n - 1] for p, n in unused_lines(record)]


def test_unused_lines_are_what_the_dataset_leaves_out():
    footer = "Synthetic test document: fictional patient, doctors and laboratory."

    assert unused_text(keyed_record("d01")) == [
        "Plot 12, Baner Road, Pune 411045", footer, "Method: Hexokinase", footer, footer,
    ]  # fmt: skip
    assert unused_text(keyed_record("d02")) == [
        "MBBS, MD (General Medicine)", "Reg. No.: MMC-2009/04/0001",
        "C/O: Fatigue and increased thirst for 2 months", footer,
    ]  # fmt: skip


def test_a_dropped_result_shows_up_as_an_unused_line():
    record = keyed_record("d01")
    record.observations = [o for o in record.observations
                           if o.printed.test_name != "MCV"]  # fmt: skip

    assert "| MCV | 76 | L | fL | 83 - 101 |" in unused_text(record)


def test_coverage_is_never_more_than_info():
    record = keyed_record("d01")
    record.observations = []

    findings = check_coverage(record)
    assert findings and {f.severity for f in findings} == {Severity.INFO}
    # The panel headings go with their results.
    assert "## COMPLETE BLOOD COUNT" in [f.details["line"] for f in findings]


# -------------------------------------------------------------------
# 9: patient match (question 5)
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "printed, expected, match",
    [
        ("Mrs. Meera Iyer", "Meera Iyer", True),
        ("MR. ROHAN KULKARNI", "Rohan Kulkarni", True),
        ("Iyer Meera", "Meera Iyer", True),
        ("M. Iyer", "Meera Iyer", True),
        ("Arjun Mehta", "Arjun Menon", False),
        ("Meera", "Meera Iyer", False),
        ("Mrs.", "Meera Iyer", False),
    ],
)
def test_names_match(printed, expected, match):
    assert names_match(printed, expected) is match


def test_read_sex_and_age():
    assert [read_sex(s) for s in ["F", "Female", "m", "MALE", "X"]] == [
        "F",
        "F",
        "M",
        "M",
        None,
    ]
    assert [read_age(a) for a in ["57 Y", "45 Years", "57", "44 yrs.", "6 M", "10 D"]] == [
        57, 45, 57, 44, None, None]  # fmt: skip


def test_the_mismatched_report_is_flagged_on_name_and_age_not_its_lab_id():
    findings = check_patient_match(keyed_record("d16"), expected_patient("d16"))

    assert problems(findings) == {
        ("patient_match", "patient", "name"),
        ("patient_match", "patient", "age"),
    }


def test_each_known_detail_is_compared():
    record = keyed_record("d06")  # hospital lab: UHID is the hospital's ID
    want = expected_patient("d06")

    assert check_patient_match(record, want) == []
    other = ExpectedPatient(name=want.name, sex="F", date_of_birth=date(1990, 1, 1),
                            mrn="SH-999999")  # fmt: skip
    assert problems(check_patient_match(record, other)) == {
        ("patient_match", "patient", "sex"),
        ("patient_match", "patient", "age"),
        ("patient_match", "patient", "patient_id"),
    }


def test_age_is_compared_within_a_year():
    record = keyed_record("d06")  # printed "44 Years" on 03/02/2025
    born = expected_patient("d06").date_of_birth

    for shift, flagged in [(0, False), (1, False), (-1, False), (2, True)]:
        want = ExpectedPatient(date_of_birth=born.replace(year=born.year - shift))
        assert bool(check_patient_match(record, want)) is flagged, shift


def test_only_what_is_known_is_compared():
    record = keyed_record("d16")

    assert check_patient_match(record, None) == []
    assert check_patient_match(record, ExpectedPatient(name="Arjun Mehta")) == []
    # An age is used when there is no date of birth.
    assert check_patient_match(record, ExpectedPatient(age_years=40)) == []
    assert check_patient_match(record, ExpectedPatient(age_years=66))


def test_a_printed_date_of_birth_is_compared():
    record = keyed_record("d06")
    cite = record.patient.printed.name.citation
    record.patient.printed.date_of_birth = PrintedText(citation=cite, text="03/08/1980")
    want = expected_patient("d06")

    assert check_patient_match(record, want) == []
    record.patient.printed.date_of_birth.text = "03/08/1981"
    assert problems(check_patient_match(record, want)) == {
        ("patient_match", "patient", "date_of_birth")
    }


# -------------------------------------------------------------------
# The pieces fit together
# -------------------------------------------------------------------
def test_validate_runs_every_validator():
    record = keyed_record("d01")
    hb = obs(record, "Haemoglobin")
    hb.printed.value_text = "10.5"  # 1
    hb.printed.flag_printed = None  # 4 (still low)
    record.document.printed.collection_date.text = "15/06/2024 08:10 AM"  # 1, 7
    record = rebuilt(record)

    found = {f.validator for f in validate(record, today=TODAY)}
    assert {Validator.SOURCE_TEXT, Validator.TEXT_LAYER, Validator.FLAG_AGREEMENT,
            Validator.DATES, Validator.COVERAGE} <= found  # fmt: skip
