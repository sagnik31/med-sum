"""The parse-step models: what the model's output must look like to be accepted."""

import copy

import pytest
from pydantic import ValidationError

from record.common import Citation
from record.printed import (
    CLASSIFIER_LABELS,
    PARSE_MODELS,
    ClassifierOutput,
    DocumentType,
    LabReportParse,
    PrescriptionParse,
    PrintedMedicine,
    PrintedRemark,
)
from record_examples import cbc_parse, cite


def test_the_example_lab_report_parse_is_valid():
    parsed = LabReportParse.model_validate(cbc_parse())

    assert [r.test_name for r in parsed.results] == ["Haemoglobin", "Platelet Count"]
    assert parsed.patient.patient_id.text == "SD-00417"


def test_values_are_kept_exactly_as_printed():
    data = cbc_parse()
    data["results"][0]["value_text"] = "  1,50,000 "
    data["results"][0]["test_name"] = "HAEMOGLOBIN (Hb)"

    result = LabReportParse.model_validate(data).results[0]

    assert result.value_text == "  1,50,000 "
    assert result.test_name == "HAEMOGLOBIN (Hb)"


def test_unknown_fields_are_refused():
    # A model cannot slip in a field nothing checks, such as its own guess.
    data = cbc_parse()
    data["results"][0]["value_num"] = 10.4

    with pytest.raises(ValidationError, match="value_num"):
        LabReportParse.model_validate(data)


def test_a_field_that_is_not_printed_must_be_null_not_missing():
    data = cbc_parse()
    del data["results"][0]["flag_printed"]

    with pytest.raises(ValidationError, match="flag_printed"):
        LabReportParse.model_validate(data)


def test_empty_text_is_refused():
    data = cbc_parse()
    data["results"][0]["unit"] = ""

    with pytest.raises(ValidationError, match="unit"):
        LabReportParse.model_validate(data)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"page": 0}, "page"),
        ({"first_line": 0}, "first_line"),
        ({"first_line": 5, "last_line": 4}, "last_line comes before first_line"),
        ({"quote": ""}, "quote"),
    ],
)
def test_citations_are_well_formed(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Citation(**{**cite(8), **overrides})


def test_a_multi_line_citation_is_accepted():
    citation = Citation(**cite(10, 11))

    assert (citation.first_line, citation.last_line) == (10, 11)


# -------------------------------------------------------------------
# Remarks (decision 25)
# -------------------------------------------------------------------
def remark(**overrides) -> dict:
    return {
        "citation": cite(10),
        "text": "Suggest iron studies.",
        "kind": "advice",
        "scope": "test",
        "test_name": "Haemoglobin",
        "panel": "COMPLETE BLOOD COUNT",
        **overrides,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"panel": None},
        {"scope": "panel", "test_name": None},
        {"scope": "document", "test_name": None, "panel": None},
    ],
    ids=["test in a panel", "test", "panel", "document"],
)
def test_remark_targets_that_match_their_scope(overrides):
    PrintedRemark(**remark(**overrides))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"test_name": None}, "needs test_name"),
        ({"scope": "panel"}, "only set when scope is test"),
        ({"scope": "panel", "test_name": None, "panel": None}, "needs panel"),
        ({"scope": "document", "test_name": None}, "has no panel"),
        ({"kind": "impression"}, "kind"),
    ],
)
def test_remark_targets_that_do_not_match_their_scope(overrides, message):
    with pytest.raises(ValidationError, match=message):
        PrintedRemark(**remark(**overrides))


# -------------------------------------------------------------------
# Prescriptions
# -------------------------------------------------------------------
def test_a_prescription_parse_with_indian_conventions():
    medicine = {
        "citation": cite(8),
        "form": "Tab.",
        "name": "Glycomet",
        "strength": "500 mg",
        "dose": "1 tab",
        "frequency": "1-0-1",
        "route": None,
        "duration": "x 30 days",
        "instructions": "after food",
    }
    data = cbc_parse()
    del data["results"]
    data["medicines"] = [medicine]

    parsed = PrescriptionParse.model_validate(data)

    assert parsed.medicines[0] == PrintedMedicine(**medicine)


def test_a_lab_report_parse_has_no_medicines_and_a_prescription_no_results():
    with pytest.raises(ValidationError, match="medicines"):
        LabReportParse.model_validate({**cbc_parse(), "medicines": []})
    data = copy.deepcopy(cbc_parse())
    data["medicines"] = []
    with pytest.raises(ValidationError, match="results"):
        PrescriptionParse.model_validate(data)


# -------------------------------------------------------------------
# Classification (decision 18)
# -------------------------------------------------------------------
def test_the_classifier_picks_from_the_fixed_labels():
    assert {label.value for label in CLASSIFIER_LABELS} == {
        "lab_report",
        "prescription",
        "other",
    }
    for label in CLASSIFIER_LABELS:
        ClassifierOutput(document_type=label.value)


@pytest.mark.parametrize("label", ["discharge_summary", "Lab Report", ""])
def test_the_classifier_refuses_other_labels(label):
    with pytest.raises(ValidationError):
        ClassifierOutput(document_type=label)


def test_every_supported_type_has_a_parse_model_and_other_does_not():
    assert set(PARSE_MODELS) == {DocumentType.LAB_REPORT, DocumentType.PRESCRIPTION}
