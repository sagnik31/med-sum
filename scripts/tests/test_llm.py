"""llm.chat: the primary model, the fallback, token caps and stop markers."""

import httpx
import ollama
import pytest

import llm

MESSAGES = [{"role": "user", "content": "Transcribe this page."}]


def models_called(client):
    return [call["model"] for call in client.calls]


def test_returns_the_primary_reply_without_calling_the_fallback(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["Haemoglobin 13.2 g/dL"]

    assert llm.chat(MESSAGES, max_tokens=100) == "Haemoglobin 13.2 g/dL"
    assert models_called(model_client) == [llm.PRIMARY_MODEL]


def test_calls_are_deterministic_streamed_and_capped(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["ok"]

    llm.chat(MESSAGES, max_tokens=123)

    call = model_client.calls[0]
    assert call["messages"] == MESSAGES
    assert call["stream"] is True
    assert call["think"] is False
    assert call["options"] == {
        "temperature": 0,
        "presence_penalty": 0,
        "num_ctx": llm.NUM_CTX,
        "num_predict": 123,
    }


@pytest.mark.parametrize(
    "error",
    [
        ollama.ResponseError("model not found", 404),
        ConnectionError("connection refused"),
        httpx.ReadTimeout("timed out"),
    ],
    ids=["response-error", "connection-error", "timeout"],
)
def test_falls_back_when_the_primary_fails(model_client, error):
    model_client.replies[llm.PRIMARY_MODEL] = [error]
    model_client.replies[llm.FALLBACK_MODEL] = ["from the fallback"]

    assert llm.chat(MESSAGES, max_tokens=100) == "from the fallback"
    assert models_called(model_client) == [llm.PRIMARY_MODEL, llm.FALLBACK_MODEL]


def test_falls_back_when_the_primary_hits_the_token_cap(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off mid-sent", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = ["complete reply"]

    assert llm.chat(MESSAGES, max_tokens=100) == "complete reply"
    assert model_client.calls[1]["messages"] == MESSAGES


def test_capped_output_is_never_returned(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [("also cut off", "length")]

    with pytest.raises(llm.ModelOutputError, match="100-token cap"):
        llm.chat(MESSAGES, max_tokens=100)


def test_error_names_each_model_and_why_it_failed(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("refused")]

    with pytest.raises(llm.ModelOutputError) as excinfo:
        llm.chat(MESSAGES, max_tokens=50)

    message = str(excinfo.value)
    assert f"{llm.PRIMARY_MODEL}: output hit the 50-token cap" in message
    assert f"{llm.FALLBACK_MODEL}: refused" in message


@pytest.mark.parametrize(
    "primary, fallback, capped",
    [
        (("cut off", "length"), ("also cut off", "length"), True),
        (("cut off", "length"), ConnectionError("refused"), True),
        (ConnectionError("refused"), ("cut off", "length"), True),
        (ConnectionError("refused"), ConnectionError("refused"), False),
    ],
    ids=["both-capped", "capped-then-down", "down-then-capped", "both-down"],
)
def test_a_cap_is_told_apart_from_every_model_being_down(
    model_client, primary, fallback, capped
):
    model_client.replies[llm.PRIMARY_MODEL] = [primary]
    model_client.replies[llm.FALLBACK_MODEL] = [fallback]

    with pytest.raises(llm.ModelOutputError) as excinfo:
        llm.chat(MESSAGES, max_tokens=50)

    assert isinstance(excinfo.value, llm.OutputCappedError) is capped


def test_a_capped_fallback_only_call_is_a_cap(model_client):
    model_client.replies[llm.FALLBACK_MODEL] = [("cut off", "length")]

    with pytest.raises(llm.OutputCappedError):
        llm.chat(MESSAGES, max_tokens=50, model_role="fallback")


def test_other_exceptions_are_not_swallowed(model_client):
    # Only model and transport errors trigger the fallback; a bug in the
    # request (or anything else) surfaces at once.
    model_client.replies[llm.PRIMARY_MODEL] = [ValueError("bad request")]

    with pytest.raises(ValueError, match="bad request"):
        llm.chat(MESSAGES, max_tokens=100)
    assert models_called(model_client) == [llm.PRIMARY_MODEL]


def test_stop_marker_ends_the_reply_and_counts_as_complete(model_client):
    # The model would have run on to the cap, but the marker came first.
    model_client.replies[llm.PRIMARY_MODEL] = [
        ("Page text\n[[END]]\nrambling on", "length")
    ]

    assert llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]") == "Page text"
    assert models_called(model_client) == [llm.PRIMARY_MODEL]


def test_reply_without_the_stop_marker_is_returned_whole(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["Page text, no marker"]

    assert (
        llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]")
        == "Page text, no marker"
    )


def test_reply_is_stripped(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["\n\n  <h2>Summary</h2>  \n"]

    assert llm.chat(MESSAGES, max_tokens=100) == "<h2>Summary</h2>"


# -------------------------------------------------------------------
# Structured output and starting at the fallback
# -------------------------------------------------------------------
def test_no_format_is_sent_unless_asked(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["ok"]

    llm.chat(MESSAGES, max_tokens=10)

    assert model_client.calls[0]["format"] is None


def test_a_schema_is_sent_as_the_format_to_each_model(model_client):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("down")]
    model_client.replies[llm.FALLBACK_MODEL] = ['{"a": "x"}']

    assert llm.chat(MESSAGES, max_tokens=10, format=schema) == '{"a": "x"}'
    assert [c["format"] for c in model_client.calls] == [schema, schema]


def test_starting_at_the_fallback_skips_the_primary(model_client):
    model_client.replies[llm.FALLBACK_MODEL] = ["from the fallback"]

    assert (
        llm.chat(MESSAGES, max_tokens=10, model_role="fallback") == "from the fallback"
    )
    assert models_called(model_client) == [llm.FALLBACK_MODEL]


def test_a_failing_fallback_has_no_model_after_it(model_client):
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("refused")]

    with pytest.raises(llm.ModelOutputError, match=f"{llm.FALLBACK_MODEL}: refused"):
        llm.chat(MESSAGES, max_tokens=10, model_role="fallback")
    assert models_called(model_client) == [llm.FALLBACK_MODEL]


def test_an_unknown_role_is_refused(model_client):
    with pytest.raises(ValueError, match="unknown model role"):
        llm.chat(MESSAGES, max_tokens=10, model_role="tertiary")


def test_model_for_names_the_configured_models():
    assert llm.model_for("primary") == llm.PRIMARY_MODEL
    assert llm.model_for("fallback") == llm.FALLBACK_MODEL
