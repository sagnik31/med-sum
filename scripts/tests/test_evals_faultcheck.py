"""
The fault check (evals/faultcheck.py): faults injected into the answer
keys, run through the validators and signals, no model. The committed
baseline is the gate: a fault's flagged rate may not drop below it.
"""

import json

import pytest

from evals import faultcheck as fc
from evals.keys import documents


@pytest.fixture(scope="module")
def check() -> fc.FaultCheck:
    return fc.run()


# -------------------------------------------------------------------
# The faults themselves
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "swapped"),
    [("14.8", "41.8"), ("1.25", "1.52"), ("7", None), ("1.1", None), ("110", "101")],
)
def test_digit_swap_swaps_the_first_two_differing_digits(value, swapped):
    assert fc.digit_swap(value) == swapped


@pytest.mark.parametrize(
    ("value", "shifted"),
    [("8.9", "89"), ("1.25", "12.5"), ("0.8", "8"), ("0.05", "0.5"), ("120", "1200"),
     ("2,45,000", None), ("<0.5", None)],
)  # fmt: skip
def test_decimal_shift_moves_the_point_one_place_right(value, shifted):
    assert fc.decimal_shift(value) == shifted


@pytest.mark.parametrize(("value", "misread"), [("8.9", "8.0"), ("142", "143")])
def test_misread_digit_changes_the_last_digit(value, misread):
    assert fc.misread_digit(value) == misread


def test_a_misread_changes_the_page_line_its_quote_and_the_value_together():
    case = fc.clean_case("d09")
    clean = fc.outcome(case)
    misread = next(
        m for m in fc.mutants(case, clean) if m.fault == "transcription_misread"
    )
    i = int(misread.target.split("-")[1]) - 1
    item = misread.case.parse.results[i]
    page = misread.case.pages[item.citation.page - 1]
    line = page.lines[item.citation.first_line - 1]
    assert item.value_text != case.parse.results[i].value_text
    assert item.citation.quote == line
    assert f"| {item.value_text} |" in line


def test_faults_never_change_the_clean_case():
    case = fc.clean_case("d01")
    before = case.parse.model_dump_json()
    pages = [list(p.lines) for p in case.pages]
    for _ in fc.mutants(case, fc.outcome(case)):
        pass
    assert case.parse.model_dump_json() == before
    assert [list(p.lines) for p in case.pages] == pages


def test_a_document_already_for_another_patient_gets_no_wrong_patient_fault():
    case = fc.clean_case("d16")
    faults = {m.fault for m in fc.mutants(case, fc.outcome(case))}
    assert "wrong_patient" not in faults


def test_an_unparsed_document_gets_no_faults():
    case = fc.clean_case("d17")
    assert list(fc.mutants(case, fc.outcome(case))) == []


# -------------------------------------------------------------------
# The run
# -------------------------------------------------------------------
def test_clean_documents_end_as_the_manifest_expects(check):
    assert [c.doc_id for c in check.clean] == list(documents())
    assert [c for c in check.clean if not c.right] == []


def test_every_fault_is_injected_somewhere(check):
    assert {r.fault for r in check.results} == {
        "date_swap", "decimal_shift", "digit_swap", "dropped_medicine",
        "dropped_result", "frequency_change", "invented_medicine",
        "invented_result", "range_bound", "remark_moved", "strength_shift",
        "transcription_misread", "unit_swap", "wrong_patient",
    }  # fmt: skip


@pytest.mark.parametrize(
    "fault",
    ["digit_swap", "decimal_shift", "unit_swap", "strength_shift", "frequency_change",
     "invented_result", "invented_medicine", "dropped_medicine", "wrong_patient",
     "date_swap", "remark_moved"],
)  # fmt: skip
def test_these_faults_are_always_flagged(check, fault):
    rows = [r for r in check.results if r.fault == fault]
    assert rows and all(r.flagged for r in rows)


def test_a_value_change_is_flagged_on_the_item_it_changed(check):
    rows = [r for r in check.results if r.fault in ("digit_swap", "unit_swap")]
    assert all(r.on_target for r in rows)


def test_known_blind_spots_are_reported_not_hidden(check):
    """
    A range bound taken for the value passes validators 1 and 2 (it is on
    the row), a dropped lab row is info only, and a misread on a page
    without a text layer has nothing to disagree with. When one of these
    is fixed, this test changes with it.
    """
    missed = {r.fault for r in check.results if not r.flagged}
    assert {"range_bound", "dropped_result"} <= missed
    image_only = [d for d, e in documents().items() if not e["has_text_layer"]]
    misreads = [
        r
        for r in check.results
        if r.fault == "transcription_misread" and r.doc_id in image_only
    ]
    # Nothing reads the page's text there, so LC-03 never fires; a misread
    # is caught only when it moves the value across its range (LC-10).
    assert misreads and not any("LC-03" in r.signals for r in misreads)
    assert any(not r.flagged for r in misreads)


# -------------------------------------------------------------------
# Gate and report
# -------------------------------------------------------------------
def test_the_committed_baseline_passes(check):
    assert fc.gate(check, fc.load_baseline()) == []


def test_the_gate_fails_when_a_fault_is_flagged_less_than_its_baseline(check):
    baseline = fc.baseline_of(check)
    rows = baseline["range_bound"]
    baseline["range_bound"] = {**rows, "flagged": rows["flagged"] + 1}
    failures = fc.gate(check, baseline)
    assert len(failures) == 1 and failures[0].startswith("range_bound: flagged")


def test_the_gate_compares_rates_when_the_counts_change(check):
    baseline = fc.baseline_of(check)
    rows = baseline["digit_swap"]
    baseline["digit_swap"] = {
        "injected": rows["injected"] * 2,
        "flagged": rows["flagged"] * 2,
    }
    assert fc.gate(check, baseline) == []


def test_the_gate_fails_for_a_fault_without_a_baseline(check):
    baseline = fc.baseline_of(check)
    del baseline["date_swap"]
    assert fc.gate(check, baseline) == [
        "date_swap: no baseline (run with --write-baseline)"
    ]


def test_the_gate_fails_when_a_clean_document_ends_elsewhere(check):
    broken = fc.FaultCheck(
        clean=[fc.CleanResult("d01", "verified", "needs_review", [], ["LC-04"])],
        results=check.results,
    )
    assert fc.gate(broken, fc.baseline_of(check)) == [
        "d01 ends needs_review ['LC-04'], expected verified []"
    ]


def test_the_report_lists_counts_intervals_and_misses(check):
    report = fc.build_report(check, fc.baseline_of(check))
    json.dumps(report)
    md = fc.render_markdown(report)
    assert "| Fault | Injected | Flagged |" in md
    assert "95% CI" in md
    assert "- **range_bound**" in md
    assert "**Gate:** pass" in md


def test_main_writes_the_report_and_passes_against_the_baseline(tmp_path):
    assert fc.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.md").exists()
