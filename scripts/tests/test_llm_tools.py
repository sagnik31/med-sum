"""
llm.chat_with_tools: one turn of a tool-using exchange on one model.
"""

import httpx
import pytest
from conftest import ToolReply

import llm
import model_health
from tools import TOOLS, specs

MESSAGES = [{"role": "user", "content": "Check lines 4 to 6 of page 1."}]
SPECS = specs(TOOLS)


@pytest.fixture
def recorded(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(llm, "record_model_call", calls.append)
    return calls


def test_tool_calls_come_back_as_the_model_sent_them(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": 1, "first": 4, "last": 6}))
    ]
    turn = llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)
    assert turn.model == llm.PRIMARY_MODEL and turn.model_role == "primary"
    assert turn.tool_calls == (
        llm.ToolCall("get_lines", {"page": 1, "first": 4, "last": 6}),
    )
    assert turn.content == ""


def test_the_tools_are_offered_and_the_options_are_the_usual_ones(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["No tool needed."]
    turn = llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)
    assert turn.content == "No tool needed." and turn.tool_calls == ()
    (sent,) = model_client.calls
    assert sent["tools"] == SPECS
    assert sent["format"] is None and sent["think"] is False
    assert sent["options"]["temperature"] == 0
    assert sent["options"]["num_predict"] == 200


def test_chat_offers_no_tools(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["ok"]
    llm.chat(MESSAGES, max_tokens=10)
    assert "tools" not in model_client.calls[0]


def test_the_fallback_role_calls_only_the_fallback(model_client):
    model_client.replies[llm.FALLBACK_MODEL] = [ToolReply(("get_lines", {}))]
    turn = llm.chat_with_tools(
        MESSAGES, tools=SPECS, max_tokens=200, model_role="fallback"
    )
    assert turn.model == llm.FALLBACK_MODEL
    assert [c["model"] for c in model_client.calls] == [llm.FALLBACK_MODEL]


def test_a_failing_model_is_not_followed_by_the_other(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [httpx.ConnectError("refused")]
    with pytest.raises(llm.ModelOutputError) as info:
        llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)
    assert not isinstance(info.value, llm.OutputCappedError)
    assert [c["model"] for c in model_client.calls] == [llm.PRIMARY_MODEL]


def test_a_capped_turn_raises_the_cap(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("partial", "length")]
    with pytest.raises(llm.OutputCappedError):
        llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=5)


def test_a_disabled_model_is_not_called(model_client, monkeypatch):
    monkeypatch.setattr(model_health, "allow", lambda model, role: False)
    with pytest.raises(llm.ModelUnavailableError):
        llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)
    assert model_client.calls == []


def test_a_bug_is_raised_as_it_is(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [KeyError("bug")]
    with pytest.raises(KeyError):
        llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)


def test_the_turn_is_recorded_with_its_tools_and_tokens(model_client, recorded):
    done = {"prompt_eval_count": 410, "eval_count": 25}
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": 1, "first": 1, "last": 2}), fields=done)
    ]
    turn = llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)
    assert (turn.prompt_tokens, turn.completion_tokens) == (410, 25)
    assert turn.wall_ms is not None
    (call,) = recorded
    assert call.status == "ok" and call.complete
    assert call.request["tools"] == ["get_lines"]
    assert call.request["structured_output"] is False
    assert (call.prompt_tokens, call.completion_tokens) == (410, 25)
    assert call.first_token_ms is not None  # a call with no text still counts


def test_a_failed_turn_is_recorded(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [httpx.ReadTimeout("slow")]
    with pytest.raises(llm.ModelOutputError):
        llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=200)
    (call,) = recorded
    assert call.status == "failed" and call.error_type == "ReadTimeout"


def test_an_unknown_role_is_refused(model_client):
    with pytest.raises(ValueError):
        llm.chat_with_tools(MESSAGES, tools=SPECS, max_tokens=10, model_role="third")
