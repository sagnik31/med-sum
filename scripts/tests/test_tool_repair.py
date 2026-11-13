"""
pipeline/tool_repair.py on its own: config, the bounded exchange's edges,
and what a tool step may store.
"""

import json

import pytest
from conftest import ToolReply

import llm
import model_health
from pipeline import tool_repair
from pipeline.tool_repair import RepairRecord, read_then_fix
from record.document import PageTranscription
from tools import ToolSource

PAGE = PageTranscription(page=1, lines=["| Sodium | 139 | mmol/L |", "| K | 4.4 |"])
SOURCE = ToolSource(pages=(PAGE,))
MESSAGES = [
    {"role": "system", "content": "parse"},
    {"role": "user", "content": "1| ..."},
    {"role": "assistant", "content": "{}"},
    {"role": "user", "content": "Problems:\n- obs-1"},
]


def exchange(budget=3, try_fix=None):
    record = RepairRecord(
        attempt=2, part=0, model_role="primary", variant="tools", turns=0
    )
    finals = []

    def final_call(messages):
        finals.append(messages)
        return "final result"

    result = read_then_fix(
        MESSAGES,
        source=SOURCE,
        model_role="primary",
        max_tokens=100,
        budget=budget,
        record=record,
        try_fix=try_fix or (lambda text: "fixed" if text == "FIX" else None),
        final_call=final_call,
    )
    return result, record, finals


# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [(None, "fixed"), ("", "fixed"), ("TOOLS", "tools"), ("fixed", "fixed")],
)
def test_the_variant_comes_from_the_environment(value, expected):
    env = {} if value is None else {"MEDSUM_REPAIR": value}
    assert tool_repair.variant(env) == expected


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        tool_repair.variant({"MEDSUM_REPAIR": "agent"})


def test_the_turn_budget_defaults_to_three_and_is_at_least_one():
    assert tool_repair.turn_budget({}) == 3
    assert tool_repair.turn_budget({"MEDSUM_REPAIR_TOOL_TURNS": "2"}) == 2
    with pytest.raises(ValueError):
        tool_repair.turn_budget({"MEDSUM_REPAIR_TOOL_TURNS": "0"})


# -------------------------------------------------------------------
# The exchange
# -------------------------------------------------------------------
def test_a_fix_in_the_first_turn_ends_the_exchange(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["FIX"]
    result, record, finals = exchange()
    assert result == "fixed" and finals == []
    assert (record.turns, record.fix_turn, record.tool_calls) == (1, "reading", 0)


def test_a_fenced_fix_is_unwrapped(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["```json\nFIX\n```"]
    assert exchange()[0] == "fixed"


def test_a_reply_that_is_neither_a_fix_nor_a_call_goes_to_the_last_turn(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["I will check the lines."]
    result, record, finals = exchange()
    assert result == "final result" and record.turns == 2
    assert "tools returned" not in finals[0][-1]["content"]


def test_the_last_turn_keeps_the_request_and_adds_the_tool_exchange(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": 1, "first": 1, "last": 1})),
        ToolReply(("search_transcription", {"text": "K"})),
    ]
    result, record, (final,) = exchange()
    assert record.turns == 3 and record.tool_calls == 2
    assert len(final) == len(MESSAGES)  # one request, not extra user turns
    request = final[-1]["content"]
    assert request.startswith("Problems:\n- obs-1")
    assert "You called get_lines with" in request and "139" in request
    assert request.endswith(tool_repair.FINAL_NOTE)
    # The model was told its budget; the reading note is not repeated at the end.
    first = model_client.calls[0]["messages"][-1]["content"]
    assert "3 turns in all" in first and "3 turns in all" not in request


def test_a_budget_of_one_is_the_last_turn_alone(model_client):
    result, record, finals = exchange(budget=1)
    assert result == "final result" and model_client.calls == []
    assert record.turns == 1


def test_calls_past_the_limit_in_one_turn_are_not_run(model_client):
    calls = [("get_lines", {"page": 1, "first": 1, "last": 1})] * 5
    model_client.replies[llm.PRIMARY_MODEL] = [ToolReply(*calls), "FIX"]
    result, record, _ = exchange()
    assert result == "fixed"
    assert (record.tool_calls, record.over_limit) == (3, 2)
    answers = model_client.calls[1]["messages"][-5:]
    assert [json.loads(a["content"]).get("error") for a in answers][-2:] == [
        "over_limit", "over_limit",
    ]  # fmt: skip


def test_a_cut_off_reading_turn_is_used_and_the_last_turn_follows(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("partial", "length")]
    result, record, finals = exchange()
    assert result == "final result" and record.turns == 2


def test_a_model_that_is_not_called_leaves_the_last_turn_to_chat(
    model_client, monkeypatch
):
    monkeypatch.setattr(model_health, "allow", lambda model, role: False)
    result, record, finals = exchange()
    assert result == "final result" and record.turns == 1 and len(finals) == 1


def test_string_numbers_are_counted(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": "1", "first": "1", "last": "2"})), "FIX",
    ]  # fmt: skip
    _, record, _ = exchange()
    assert record.inexact_types == 1 and record.malformed == 0


def test_get_text_layer_is_offered_only_when_a_page_has_one(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["FIX"]
    exchange()
    offered = {t["function"]["name"] for t in model_client.calls[0]["tools"]}
    assert offered == {"get_lines", "search_transcription", "get_item"}


def test_a_tool_never_offered_to_repair_is_unknown():
    result = tool_repair.run_tool("list_items", {}, SOURCE)
    assert result.error.kind == "unknown_tool"


# -------------------------------------------------------------------
# What a tool step stores of the arguments
# -------------------------------------------------------------------
def test_numbers_are_stored_and_text_only_as_a_hash():
    ref = tool_repair._argument_ref({"page": 1, "text": "Ms. Example Patient"})
    assert ref["page"] == 1
    assert set(ref["text"]) == {"sha256", "chars"}
    assert "Example" not in json.dumps(ref)


def test_arguments_sent_as_text_are_stored_as_a_hash():
    assert set(tool_repair._argument_ref("not json")["unparsed"]) == {"sha256", "chars"}
    assert tool_repair._argument_ref([1, 2]) == {"type": "list"}
