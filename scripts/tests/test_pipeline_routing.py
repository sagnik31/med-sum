"""
The graph's routing functions (pipeline/routing.py), one test per branch.
They are pure functions of the state, so no run, model or database.
"""

import pytest

from pipeline import routing
from pipeline.routing import MAX_PARSE_ATTEMPTS
from pipeline.state import PageImage
from record.document import PageTranscription
from record.printed import DocumentType
from validators import Finding, Severity, Validator

LAB, RX, OTHER = DocumentType.LAB_REPORT, DocumentType.PRESCRIPTION, DocumentType.OTHER


def image(n: int) -> PageImage:
    return PageImage(page=n, sha256="0" * 64, size_bytes=1, width=1, height=1)


def page(n: int) -> PageTranscription:
    return PageTranscription(page=n, lines=["x"])


def finding(severity: Severity) -> Finding:
    return Finding(validator=Validator.SOURCE_TEXT, severity=severity, message="m")


# -------------------------------------------------------------------
# transcribe and classify
# -------------------------------------------------------------------
def test_transcribe_again_while_a_page_is_left():
    state = {"pages": [image(1), image(2)], "transcriptions": [page(1)]}
    assert routing.after_transcribe(state) == "transcribe"


def test_classify_once_every_page_is_transcribed():
    state = {"pages": [image(1), image(2)], "transcriptions": [page(2), page(1)]}
    assert routing.after_transcribe(state) == "classify"


@pytest.mark.parametrize("doc_type, expected",
                         [(LAB, "parse"), (RX, "parse"), (OTHER, "validate")])  # fmt: skip
def test_parsed_types_are_parsed_and_other_is_not(doc_type, expected):
    assert routing.after_classify({"doc_type": doc_type}) == expected


# -------------------------------------------------------------------
# after parse or repair
# -------------------------------------------------------------------
def test_a_parse_that_fit_is_validated():
    assert routing.after_parse({"parse": object(), "parse_attempts": 1}) == "validate"


@pytest.mark.parametrize("attempts", range(1, MAX_PARSE_ATTEMPTS))
def test_a_failed_parse_is_repaired_while_attempts_are_left(attempts):
    state = {"parse": None, "parse_attempts": attempts, "model_role": "primary"}
    assert routing.after_parse(state) == "repair"


def test_the_primary_s_last_failed_attempt_goes_to_the_fallback():
    state = {"parse": None, "parse_attempts": MAX_PARSE_ATTEMPTS,
             "model_role": "primary"}  # fmt: skip
    assert routing.after_parse(state) == "fallback"


def test_the_fallback_is_repaired_with_a_budget_of_its_own():
    state = {"parse": None, "parse_attempts": 1, "model_role": "fallback"}
    assert routing.after_parse(state) == "repair"


def test_giving_up_keeps_an_earlier_record():
    state = {"parse": None, "parse_attempts": MAX_PARSE_ATTEMPTS,
             "model_role": "fallback", "record_parsed": True}  # fmt: skip
    assert routing.after_parse(state) == "render"


def test_giving_up_without_any_record_builds_one_of_the_pages():
    state = {"parse": None, "parse_attempts": MAX_PARSE_ATTEMPTS,
             "model_role": "fallback"}  # fmt: skip
    assert routing.after_parse(state) == "validate"


# -------------------------------------------------------------------
# after validate
# -------------------------------------------------------------------
@pytest.mark.parametrize("severity", [Severity.FLAG, Severity.INFO])
def test_flags_and_info_go_on_without_a_repair(severity):
    state = {"record_parsed": True, "findings": [finding(severity)],
             "parse_attempts": 1, "model_role": "primary"}  # fmt: skip
    assert routing.after_validate(state) == "render"


def test_no_findings_go_on():
    assert routing.after_validate({"record_parsed": True, "findings": []}) == "render"


def test_an_error_is_repaired():
    state = {"record_parsed": True, "findings": [finding(Severity.ERROR)],
             "parse_attempts": 1, "model_role": "primary"}  # fmt: skip
    assert routing.after_validate(state) == "repair"


def test_an_error_left_by_the_primary_goes_to_the_fallback():
    state = {"record_parsed": True, "findings": [finding(Severity.ERROR)],
             "parse_attempts": MAX_PARSE_ATTEMPTS, "model_role": "primary"}  # fmt: skip
    assert routing.after_validate(state) == "fallback"


def test_an_error_left_by_the_fallback_goes_to_review_with_its_record():
    state = {"record_parsed": True, "findings": [finding(Severity.ERROR)],
             "parse_attempts": MAX_PARSE_ATTEMPTS, "model_role": "fallback"}  # fmt: skip
    assert routing.after_validate(state) == "render"


def test_a_record_of_pages_alone_is_never_sent_back():
    # Type other, or every parse failed: there is no parse to repair.
    state = {"record_parsed": False, "findings": [finding(Severity.ERROR)],
             "parse_attempts": 1, "model_role": "primary"}  # fmt: skip
    assert routing.after_validate(state) == "render"


def test_the_budget_is_the_first_parse_and_two_repairs():
    assert MAX_PARSE_ATTEMPTS == 3
