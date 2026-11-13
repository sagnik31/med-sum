"""
The repair eval: its cases (evals/repair_cases.py) and its runner and
report (evals/repair.py), with the fake model client.
"""

import json

import pytest
from conftest import ToolReply

import llm
from evals import repair, repair_cases
from evals.keys import answer_key, parse_of
from evals.repair import CaseResult, summarise, verdict

PRIMARY = llm.PRIMARY_MODEL
CASE = "d04-date_swap-1"  # one part: one reply per repair


# -------------------------------------------------------------------
# The cases
# -------------------------------------------------------------------
@pytest.fixture(scope="module")
def built():
    return repair_cases.build()


def test_the_committed_cases_are_in_step_with_the_dataset_and_faults(built):
    committed = repair_cases.CASES_FILE.read_text(encoding="utf-8")
    assert committed == repair_cases.as_json(built), (
        "rebuild with: python -m evals.repair_cases --write"
    )


def test_every_case_has_an_error_to_repair_and_a_fault_a_parse_can_fix(built):
    assert built
    assert all(c.errors for c in built)
    assert not {c.fault for c in built} & set(repair_cases.NOT_REPAIRABLE)
    assert len({c.id for c in built}) == len(built)


def test_core_and_extended_take_one_case_of_each_fault_from_their_split(built):
    faults = {c.fault for c in built}
    for subset, split in (("core", "test"), ("extended", "held_out")):
        chosen = [c for c in built if c.subset == subset]
        assert {c.split for c in chosen} == {split}
        assert len(chosen) == len({c.fault for c in chosen})
        assert {c.fault for c in chosen} <= faults
    core = [c for c in built if c.subset == "core"]
    assert len({c.doc_id for c in core}) > len(core) // 2  # spread over documents


def test_subsets_nest():
    cases = repair_cases.load()
    core = repair_cases.in_subset(cases, "core")
    extended = repair_cases.in_subset(cases, "extended")
    assert set(map(id, core)) <= set(map(id, extended))
    assert len(repair_cases.in_subset(cases, "all")) == len(cases)
    with pytest.raises(ValueError):
        repair_cases.in_subset(cases, "some")


def test_a_case_is_rebuilt_with_its_fault():
    case = repair_cases.case_parse(CASE)
    clean = parse_of(answer_key("d04"))
    assert case.parse != clean
    assert case.doc_id == "d04"


def test_a_changed_fault_is_refused(monkeypatch):
    cases = repair_cases.load()
    stale = cases[CASE].__class__(**{**cases[CASE].__dict__, "fingerprint": "sha256:0"})
    monkeypatch.setattr(repair_cases, "load", lambda *a: {**cases, CASE: stale})
    with pytest.raises(ValueError, match="rebuild"):
        repair_cases.case_parse(CASE)


# -------------------------------------------------------------------
# Rows and the verdict (no database)
# -------------------------------------------------------------------
def result(variant, repaired=True, repairs=1, unsafe=0, seconds=10.0, **tools):
    log = {
        "variant": variant, "model_calls": 1, "prompt_tokens": 100,
        "completion_tokens": 50, "seconds": seconds, "turns": 1, "by_tool": {},
        "fix_turn": None, "tool_calls": 0, "malformed": 0, "unknown_tool": 0,
        "not_found": 0, "inexact_types": 0, "over_limit": 0, **tools,
    }  # fmt: skip
    return CaseResult(
        case="c", fault="digit_swap", split="test", model=PRIMARY, variant=variant,
        repaired=repaired, repairs=repairs, unsafe_after=unsafe, seconds=seconds,
        log=[log] * repairs,
    )  # fmt: skip


def test_a_row_counts_repairs_tokens_and_seconds_per_repair():
    row = summarise([result("fixed"), result("fixed", repaired=False, repairs=2)])
    assert row["cases"] == 2 and row["repaired"]["hits"] == 1
    assert row["repaired_first_try"] == 1
    assert row["repairs"] == 3 and row["repairs_per_case"] == 1.5
    assert row["tokens_per_repair"] == 150 and row["seconds_per_repair"] == 10.0
    assert "tools" not in row


def test_a_tool_row_counts_its_calls():
    calls = {
        "tool_calls": 2,
        "malformed": 1,
        "by_tool": {"get_lines": 2},
        "fix_turn": "reading",
    }
    row = summarise([result("tools", **calls)])
    tools = row["tools"]
    assert tools["tool_calls"] == 2 and tools["malformed"] == 1
    assert tools["well_formed_rate"] == 0.5
    assert tools["by_tool"] == {"get_lines": 2}
    assert tools["fix_in_reading_turn"] == 1


@pytest.mark.parametrize(
    "tools, supports",
    [
        (dict(), "tools"),
        (dict(repaired=False), "fixed"),
        (dict(unsafe=1), "fixed"),
        (dict(seconds=11.0), "fixed"),
    ],
)
def test_the_tool_repair_is_supported_only_if_no_worse_on_every_count(tools, supports):
    rows = {
        "fixed": summarise([result("fixed")]),
        "tools": summarise([result("tools", **tools)]),
    }
    assert verdict(rows)["supports"] == supports


def test_without_both_variants_the_verdict_is_the_fixed_one():
    assert verdict({"tools": summarise([result("tools")])})["supports"] == "fixed"


# -------------------------------------------------------------------
# The runner, through the graph's nodes (database)
# -------------------------------------------------------------------
@pytest.fixture
def test_database(db, monkeypatch):
    """The test database stands in for the eval runner's own."""
    from db_rows import add_account

    class TestDatabase:
        user_id = add_account(db)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(repair, "EvalDatabase", TestDatabase)


def clean_reply() -> str:
    return parse_of(answer_key("d04")).model_dump_json()


def wrong_reply() -> str:
    return repair_cases.case_parse(CASE).parse.model_dump_json()


@pytest.mark.db
def test_a_live_run_records_and_a_replay_gives_the_same_report(
    test_database, model_client, tmp_path
):
    model_client.digests[PRIMARY] = "sha256:abc"
    model_client.replies[PRIMARY] = [
        clean_reply(),  # fixed: the first repair fixes it
        ToolReply(("get_lines", {"page": 1, "first": 1, "last": 5})),
        clean_reply(),  # tools: a read, then the fix
    ]
    live = tmp_path / "live"
    args = ["--model", PRIMARY, "--cases", CASE, "--cassettes", str(tmp_path / "c")]
    assert repair.main(["--live", *args, "--out", str(live), "--no-history"]) == 0
    report = json.loads((live / "report.json").read_text(encoding="utf-8"))
    rows = report["models"][PRIMARY]["rows"]
    assert rows["fixed"]["repaired"]["hits"] == rows["tools"]["repaired"]["hits"] == 1
    assert rows["fixed"]["unsafe_after"] == 0
    assert rows["tools"]["tools"]["by_tool"] == {"get_lines": 1}
    assert rows["tools"]["repairs"] == 1
    calls = len(model_client.calls)
    assert calls == 3

    out = tmp_path / "replay"
    assert repair.main(["--replay", *args, "--out", str(out)]) == 0
    replayed = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert len(model_client.calls) == calls  # no model call

    def strip(r):
        return [{k: v for k, v in c.items() if k != "seconds"} for c in r["results"]]  # noqa: E731

    for r in (report, replayed):
        for c in r["results"]:
            c["log"] = [
                {k: v for k, v in x.items() if k != "seconds"} for x in c["log"]
            ]
    assert strip(replayed) == strip(report)


@pytest.mark.db
def test_a_fault_the_model_keeps_uses_the_attempts_and_is_not_repaired(
    test_database, model_client, tmp_path
):
    model_client.replies[PRIMARY] = [wrong_reply(), wrong_reply()]
    out = tmp_path / "r"
    assert repair.main([
        "--live", "--model", PRIMARY, "--cases", CASE, "--variant", "fixed",
        "--cassettes", str(tmp_path / "c"), "--out", str(out), "--no-history",
    ]) == 0  # fmt: skip
    (case,) = json.loads((out / "report.json").read_text(encoding="utf-8"))["results"]
    assert case["repaired"] is False and case["repairs"] == 2
    assert case["stopped_at"] == "fallback"  # where the graph would go next
    assert case["errors_after"] == case["errors_before"] > 0
