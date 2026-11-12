"""
The results history (evals/history.py) and the gate between two runs
(evals/compare.py), on the self-check report so no model is needed.
"""

import copy
import json

import pytest

from evals import compare, history, selfcheck
from evals.keys import manifest
from evals.report import build_report, write_report


@pytest.fixture(scope="module")
def report() -> dict:
    return build_report(
        "self-check",
        [selfcheck.selfcheck_run()],
        manifest(),
        outcomes={selfcheck.LABEL: selfcheck.selfcheck_outcomes()},
    )


@pytest.fixture(scope="module")
def entry(report) -> dict:
    return history.entry(report, mode="live", digests={"m": "sha256:1"})


# -------------------------------------------------------------------
# History
# -------------------------------------------------------------------
def test_an_entry_records_what_produced_it(entry):
    assert entry["mode"] == "live"
    assert entry["digests"] == {"m": "sha256:1"}
    assert set(entry["prompts"]) >= {"extract_page", "parse_lab_report"}
    assert all(len(h) == 64 for h in entry["prompts"].values())
    assert entry["code"] is None or len(entry["code"]["commit"]) == 40


def test_an_entry_holds_counts_not_content(entry):
    m = entry["models"][selfcheck.LABEL]
    assert (m["field_f1"], m["unsafe"], m["clinical_items"]) == (1.0, 0, 104)
    assert m["routing"]["auto_verified"] == {"hits": 14, "total": 17}
    assert m["routing"]["unsafe_in_verified"] == {"hits": 0, "total": 99}
    assert set(m["splits"]) == {"dev", "test"}
    text = json.dumps(entry)
    assert "Fatima" not in text and "TSH" not in text


def test_entries_are_written_loaded_and_shown(entry, tmp_path):
    path = history.write_entry(entry, tmp_path)
    assert path.name.endswith("-self-check.json")
    assert history.load(tmp_path) == [entry]
    board = history.scoreboard(history.load(tmp_path))
    assert "| `answer keys` | 100.0% | 0/104 |" in board
    assert "14/17" in board


def test_a_report_can_be_read_as_an_entry(report, tmp_path):
    json_path, _ = write_report(report, tmp_path)
    read = history.read(json_path)
    assert read["models"] == history.entry(report, mode="x")["models"]


def test_anything_else_is_not_an_entry(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        history.read(path)


# -------------------------------------------------------------------
# Compare
# -------------------------------------------------------------------
def _model(e: dict) -> dict:
    return e["models"][selfcheck.LABEL]


def test_a_run_passes_against_itself(entry):
    assert all(c.ok for c in compare.compare(entry, entry))


def test_unsafe_errors_may_never_rise(entry):
    worse = copy.deepcopy(entry)
    _model(worse)["unsafe"] = 1
    failed = [c.metric for c in compare.compare(entry, worse) if not c.ok]
    assert failed == ["unsafe"]


def test_unsafe_errors_in_verified_documents_may_never_rise(entry):
    worse = copy.deepcopy(entry)
    _model(worse)["routing"]["unsafe_in_verified"]["hits"] = 1
    failed = [c.metric for c in compare.compare(entry, worse) if not c.ok]
    assert failed == ["unsafe_in_verified"]


def test_f1_may_fall_within_its_tolerance_only(entry):
    small, large = copy.deepcopy(entry), copy.deepcopy(entry)
    _model(small)["field_f1"] = 0.995
    _model(large)["field_f1"] = 0.98
    assert all(c.ok for c in compare.compare(entry, small))
    assert [c.metric for c in compare.compare(entry, large) if not c.ok] == ["field_f1"]


def test_routing_may_not_get_worse(entry):
    worse = copy.deepcopy(entry)
    _model(worse)["routing"]["end_state_right"]["hits"] = 16
    assert [c.metric for c in compare.compare(entry, worse) if not c.ok] == [
        "end_state_right"
    ]


def test_a_model_missing_from_the_second_run_fails(entry):
    other = copy.deepcopy(entry)
    other["models"] = {}
    (check,) = compare.compare(entry, other)
    assert not check.ok and check.rule == "missing in B"


def test_the_command_prints_the_table_and_exits_by_the_gate(entry, tmp_path, capsys):
    a = history.write_entry(entry, tmp_path / "a")
    worse = copy.deepcopy(entry)
    _model(worse)["unsafe"] = 2
    b = history.write_entry(worse, tmp_path / "b")
    assert compare.main([str(a), str(a)]) == 0
    assert compare.main([str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "**FAIL**" in out and "| unsafe | 0 | 2 | +2 |" in out
