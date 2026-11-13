"""
Cassettes (evals/cassettes.py): model replies recorded through llm.chat
and replayed without Ollama, keyed by the whole request, with a miss that
says what changed.
"""

import pytest

import llm
from evals.cassettes import (
    Cassette,
    CassetteMiss,
    RecordingClient,
    ReplayClient,
    request_key,
)

MODEL = llm.PRIMARY_MODEL
OPTIONS = {"temperature": 0, "num_predict": 10}


def messages(system="Read the page.", user="Page one.", image=b"\x89PNG..."):
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user, "images": [image]},
    ]


def ask(client, msgs, options=OPTIONS, format=None):
    stream = client.chat(model=MODEL, messages=msgs, stream=True, think=False,
                         options=options, format=format)  # fmt: skip
    return "".join(c.message.content or "" for c in stream)


@pytest.fixture
def recorded(model_client) -> Cassette:
    """One request recorded from the fake model."""
    model_client.digests[MODEL] = "sha256:abc"
    model_client.replies[MODEL] = [("the page", "stop", {"eval_count": 3})]
    cassette = Cassette()
    assert ask(RecordingClient(model_client, cassette), messages()) == "the page"
    return cassette


# -------------------------------------------------------------------
# Keys
# -------------------------------------------------------------------
def test_the_key_hashes_images_and_is_stable():
    a = request_key(model=MODEL, messages=messages(), options=OPTIONS)
    b = request_key(model=MODEL, messages=messages(), options=dict(OPTIONS))
    assert a == b
    other = request_key(model=MODEL, messages=messages(image=b"other"), options=OPTIONS)
    assert other.request != a.request and other.input != a.input
    assert other.prompt == a.prompt


def test_the_prompt_is_the_system_text():
    a = request_key(model=MODEL, messages=messages(), options=OPTIONS)
    b = request_key(model=MODEL, messages=messages(system="Changed."), options=OPTIONS)
    assert (b.prompt != a.prompt, b.input == a.input) == (True, True)


# -------------------------------------------------------------------
# Record and replay
# -------------------------------------------------------------------
def test_a_recording_keeps_the_reply_digest_and_counts(recorded):
    (entry,) = recorded.entries
    assert (entry.model, entry.digest, entry.reply) == (MODEL, "sha256:abc", "the page")
    assert entry.done_reason == "stop"
    assert entry.done == {"eval_count": 3}


def test_replay_answers_without_the_model(recorded, model_client):
    calls = len(model_client.calls)
    assert ask(ReplayClient(recorded), messages()) == "the page"
    assert len(model_client.calls) == calls


def test_llm_chat_records_and_replays_the_same(model_client, monkeypatch):
    model_client.digests[MODEL] = "sha256:abc"
    model_client.replies[MODEL] = ["the page"]
    cassette = Cassette()
    monkeypatch.setattr(llm, "_client", RecordingClient(model_client, cassette))
    assert llm.chat(messages(), max_tokens=10) == "the page"

    monkeypatch.setattr(llm, "_client", ReplayClient(cassette))
    monkeypatch.setattr(llm, "_digests", {})
    assert llm.chat(messages(), max_tokens=10) == "the page"
    assert llm.model_digest(MODEL) == "sha256:abc"
    assert len(model_client.calls) == 1


def test_a_cassette_survives_saving_and_loading(recorded, tmp_path):
    path = tmp_path / "m" / "d01.json"
    recorded.save(path)
    assert Cassette.load(path) == recorded


def test_a_cassette_of_another_version_is_refused(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"version": 99, "entries": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        Cassette.load(path)


# -------------------------------------------------------------------
# Misses
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"msgs": messages(system="A new prompt.")}, "the prompt changed"),
        ({"msgs": messages(image=b"another page")}, "the input changed"),
        (
            {"options": {**OPTIONS, "num_predict": 20}},
            "options, output schema or tools",
        ),
        ({"format": {"type": "object"}}, "options, output schema or tools"),
    ],
)
def test_a_miss_says_what_changed(recorded, change, why):
    replay = ReplayClient(recorded)
    with pytest.raises(CassetteMiss, match=why) as e:
        ask(
            replay,
            change.get("msgs", messages()),
            options=change.get("options", OPTIONS),
            format=change.get("format"),
        )
    assert "re-record" in str(e.value)


def test_a_request_repeated_more_than_recorded_misses(recorded):
    replay = ReplayClient(recorded)
    ask(replay, messages())
    with pytest.raises(CassetteMiss, match="more often than recorded"):
        ask(replay, messages())


def test_another_model_build_is_refused(recorded):
    replay = ReplayClient(recorded, expect_digests={MODEL: "sha256:new"})
    with pytest.raises(CassetteMiss, match="digest"):
        ask(replay, messages())


def test_a_miss_is_not_a_model_error_so_llm_raises_it(recorded, monkeypatch):
    monkeypatch.setattr(llm, "_client", ReplayClient(recorded))
    with pytest.raises(CassetteMiss):
        llm.chat(messages(system="Changed."), max_tokens=10)


# -------------------------------------------------------------------
# Tool turns
# -------------------------------------------------------------------
TOOL_SPECS = [{"type": "function", "function": {"name": "get_lines", "parameters": {}}}]


def test_the_tools_offered_are_part_of_the_key():
    plain = request_key(model=MODEL, messages=messages(), options=OPTIONS)
    offered = request_key(
        model=MODEL, messages=messages(), options=OPTIONS, tools=TOOL_SPECS
    )
    assert plain.request != offered.request and plain.options != offered.options
    assert plain.prompt == offered.prompt and plain.input == offered.input


def test_a_key_without_tools_is_unchanged_by_the_tools_option():
    # Cassettes recorded before tools existed still match a call without them.
    assert request_key(model=MODEL, messages=messages(), options=OPTIONS) == (
        request_key(model=MODEL, messages=messages(), options=OPTIONS, tools=None)
    )


def test_a_tool_turn_is_recorded_and_replayed_with_its_calls(model_client, monkeypatch):
    from conftest import ToolReply

    model_client.digests[MODEL] = "sha256:abc"
    call = ("get_lines", {"page": 1, "first": 2, "last": 3})
    model_client.replies[MODEL] = [ToolReply(call, fields={"eval_count": 7})]
    cassette = Cassette()
    msgs = [{"role": "user", "content": "check line 2"}]
    monkeypatch.setattr(llm, "_client", RecordingClient(model_client, cassette))
    live = llm.chat_with_tools(msgs, tools=TOOL_SPECS, max_tokens=10)
    (entry,) = cassette.entries
    assert entry.tool_calls == [{"name": "get_lines", "arguments": call[1]}]
    assert model_client.calls[0]["tools"] == TOOL_SPECS

    monkeypatch.setattr(llm, "_client", ReplayClient(cassette))
    replayed = llm.chat_with_tools(msgs, tools=TOOL_SPECS, max_tokens=10)
    assert replayed.tool_calls == live.tool_calls == (llm.ToolCall(*call),)
    assert replayed.completion_tokens == 7
    assert len(model_client.calls) == 1


def test_a_tool_cassette_survives_saving_and_loading(model_client, tmp_path):
    from conftest import ToolReply

    model_client.replies[MODEL] = [ToolReply(("get_lines", {"page": 1}))]
    cassette = Cassette()
    client = RecordingClient(model_client, cassette)
    list(client.chat(model=MODEL, messages=messages(), stream=True, think=False,
                     options=OPTIONS, tools=TOOL_SPECS))  # fmt: skip
    cassette.save(tmp_path / "c.json")
    assert Cassette.load(tmp_path / "c.json").entries == cassette.entries


def test_changed_tools_are_a_miss_that_says_so(recorded):
    replay = ReplayClient(recorded)
    with pytest.raises(CassetteMiss, match="tools changed"):
        list(replay.chat(model=MODEL, messages=messages(), stream=True, think=False,
                         options=OPTIONS, tools=TOOL_SPECS))  # fmt: skip
