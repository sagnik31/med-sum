"""
The tool repair through the whole graph, with the fake model client:
MEDSUM_REPAIR=tools, a tool-chosen fix, the turn budget used up, a
malformed call, and the fixed re-parse staying the default.
"""

import json

import pytest
from conftest import ToolReply
from test_pipeline_graph import (
    PRIMARY,
    FALLBACK,
    classified,
    document,
    fixed_today,
    good_parse,
    nodes_run,
    pages_of,
    run,
    run_row,
    wrong_value,
)

from dataset_records import key
from pipeline.routing import MAX_PARSE_ATTEMPTS

pytestmark = pytest.mark.db
__all__ = ["document", "fixed_today"]  # fixtures shared with the graph tests

# d09's first result, whose value wrong_value() changes, is on page 1, line 21.
LINE = key("d09").observations[0].printed.citation.first_line
READ_ROW = ("get_lines", {"page": 1, "first": LINE - 1, "last": LINE + 1})


@pytest.fixture
def tools_repair(monkeypatch):
    monkeypatch.setenv("MEDSUM_REPAIR", "tools")


def tool_steps(db, run_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT t.input, t.output, t.status, p.node FROM pipeline_steps t"
            " JOIN pipeline_steps p ON p.id = t.parent_step_id"
            " WHERE t.run_id = %s AND t.node = 'tool' ORDER BY t.seq",
            (run_id,),
        )
        names = [c.name for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


def repair_calls(model_client) -> list[dict]:
    """The model calls after the first parse: the repair's turns."""
    return model_client.calls[len(pages_of("d09")) + 2 :]


def test_the_fixed_re_parse_is_the_default(db, document, model_client):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"), good_parse("d09"),
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    assert all("tools" not in c for c in model_client.calls)
    (log,) = final["repair_log"]
    assert (log.variant, log.turns, log.tool_calls) == ("fixed", 1, 0)
    assert log.model_calls == 1


def test_a_clean_document_never_reaches_the_tools(
    db, document, model_client, tools_repair
):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), good_parse("d09"),
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    assert all("tools" not in c for c in model_client.calls)
    assert "repair_log" not in final


def test_a_fix_the_model_finds_with_a_tool(db, document, model_client, tools_repair):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"),
        ToolReply(READ_ROW), good_parse("d09"),
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    assert final["repairs"] == 1
    first, second = repair_calls(model_client)
    offered = {t["function"]["name"] for t in first["tools"]}
    # d09 is a PDF with a text layer, so its text can be read too.
    assert offered == {
        "get_lines",
        "search_transcription",
        "get_text_layer",
        "get_item",
    }
    assert "3 turns in all" in first["messages"][-1]["content"]
    # The tool's answer went back to the model, citing the lines it read.
    answer = second["messages"][-1]
    assert answer["role"] == "tool" and answer["tool_name"] == "get_lines"
    assert json.loads(answer["content"])["cite"] == f"p1:{LINE - 1}-{LINE + 1}"
    (log,) = final["repair_log"]
    assert (log.variant, log.turns, log.tool_calls, log.fix_turn) == (
        "tools", 2, 1, "reading",
    )  # fmt: skip
    assert log.by_tool == {"get_lines": 1} and log.malformed == 0
    # The fixed item traces to the repair step, as after a fixed repair.
    assert final["record"].observations[0].provenance.node == "repair"

    run_id = run_row(db, state["document_id"])["id"]
    assert nodes_run(db, run_id)[3:8] == [
        "parse", "validate", "repair", "tool", "validate",
    ]  # fmt: skip
    (step,) = tool_steps(db, run_id)
    assert step["node"] == "repair" and step["status"] == "ok"
    assert step["input"] == {"tool": "get_lines", "arguments": READ_ROW[1]}
    assert step["output"] == {
        "tool": "get_lines", "ok": True, "lines_returned": 3, "exact_types": True,
    }  # fmt: skip
    # The step holds no page text.
    page_text = set(key("d09").pages[0].lines) - {""}
    assert not any(line in json.dumps(step) for line in page_text)


def test_when_the_reading_turns_are_used_the_last_turn_must_be_the_fix(
    db, document, model_client, tools_repair
):
    state = document("d09")
    search = ("search_transcription", {"text": "T3"})
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"),
        ToolReply(READ_ROW), ToolReply(search), good_parse("d09"),
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    *reading, last = repair_calls(model_client)
    assert len(reading) == 2 and all("tools" in c for c in reading)
    # The last turn offers no tools and enforces the parse schema, with the
    # tool exchange written into its request.
    assert "tools" not in last and last["format"] is not None
    request = last["messages"][-1]["content"]
    assert "What the tools returned:" in request
    assert (
        "You called get_lines" in request
        and "You called search_transcription" in request
    )
    assert "Your turns for reading are used" in request
    (log,) = final["repair_log"]
    assert (log.turns, log.tool_calls, log.fix_turn) == (3, 2, "final")
    assert log.by_tool == {"get_lines": 1, "search_transcription": 1}


def test_a_malformed_call_uses_its_turn_and_its_error_goes_back(
    db, document, model_client, tools_repair
):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"),
        ToolReply(("get_lines", {"page": 1, "lines": "20-22"})),
        ToolReply(("open_file", {"path": "x"})),
        good_parse("d09"),
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    _, second, last = repair_calls(model_client)
    error = json.loads(second["messages"][-1]["content"])
    assert error["error"] == "malformed_arguments"
    assert "unknown_tool" in last["messages"][-1]["content"]
    (log,) = final["repair_log"]
    assert (log.turns, log.tool_calls, log.malformed, log.unknown_tool) == (3, 2, 1, 1)
    run_id = run_row(db, state["document_id"])["id"]
    assert [s["status"] for s in tool_steps(db, run_id)] == ["failed", "failed"]


def test_a_fix_that_still_has_errors_is_judged_like_any_parse(
    db, document, model_client, tools_repair
):
    # Every repair's fix keeps the wrong value: the primary's attempts run
    # out and the fallback takes over, as after fixed repairs.
    state = document("d09")
    turns = [ToolReply(READ_ROW), wrong_value("d09")]
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"),
        *turns * (MAX_PARSE_ATTEMPTS - 1),
    ]  # fmt: skip
    model_client.replies[FALLBACK] = [good_parse("d09")]

    final = run(state)

    assert final["end_state"] == "verified"
    assert final["record"].document.provenance.model == FALLBACK
    assert [r.attempt for r in final["repair_log"]] == [2, 3]
    steps = nodes_run(db, run_row(db, state["document_id"])["id"])
    assert steps.count("repair") == MAX_PARSE_ATTEMPTS - 1
    assert steps[-5:] == ["fallback", "parse", "validate", "render", "finish"]


def test_errors_left_after_both_models_go_to_review(
    db, document, model_client, tools_repair
):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"),
        *[wrong_value("d09")] * (MAX_PARSE_ATTEMPTS - 1),
    ]  # fmt: skip
    model_client.replies[FALLBACK] = [wrong_value("d09")] * MAX_PARSE_ATTEMPTS

    final = run(state)

    assert final["end_state"] == "needs_review"
    reasons = final["record"].observations[0].provenance.low_confidence_reasons
    assert reasons[0].startswith("LC-04")
    assert {r.model_role for r in final["repair_log"]} == {"primary", "fallback"}
    assert all(r.fix_turn == "reading" for r in final["repair_log"])
