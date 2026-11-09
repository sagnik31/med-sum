"""
llm.chat records every model round trip as a ModelCall (one
pipeline_model_calls row): status, tokens, timings, sizes, digest.
"""

import base64

import fitz
import httpx
import ollama
import pytest

import llm

MESSAGES = [{"role": "user", "content": "Transcribe this page."}]

DONE = {
    "prompt_eval_count": 650,
    "eval_count": 1583,
    "total_duration": 212_400_000_000,
    "load_duration": 1_250_000_000,
    "prompt_eval_duration": 9_876_543_210,
    "eval_duration": 201_000_000_000,
}


@pytest.fixture
def recorded(monkeypatch) -> list:
    """Every ModelCall llm.chat reports, step or no step."""
    calls = []
    monkeypatch.setattr(llm, "record_model_call", calls.append)
    return calls


def summary(calls):
    return [(c.model, c.role, c.status, c.error_type) for c in calls]


# -------------------------------------------------------------------
# One call per attempt
# -------------------------------------------------------------------
def test_a_primary_that_answers_is_one_ok_call(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [("reply", "stop", DONE)]

    llm.chat(MESSAGES, max_tokens=4096)

    assert summary(recorded) == [(llm.PRIMARY_MODEL, "primary", "ok", None)]
    (call,) = recorded
    assert (call.prompt_tokens, call.completion_tokens) == (650, 1583)
    assert call.done_reason == "stop" and call.complete
    assert call.provider == "ollama"


def test_a_capped_primary_and_the_fallback_are_two_calls(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [("cut", "length", DONE)]
    model_client.replies[llm.FALLBACK_MODEL] = [("whole", "stop", {"eval_count": 9})]

    llm.chat(MESSAGES, max_tokens=4096)

    assert summary(recorded) == [
        (llm.PRIMARY_MODEL, "primary", "capped", None),
        (llm.FALLBACK_MODEL, "fallback", "ok", None),
    ]
    assert [c.completion_tokens for c in recorded] == [1583, 9]  # not summed
    assert recorded[0].done_reason == "length"


def test_a_primary_that_raised_is_a_failed_call_without_tokens(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [httpx.ReadTimeout("timed out")]
    model_client.replies[llm.FALLBACK_MODEL] = [("from the fallback", "stop", DONE)]

    llm.chat(MESSAGES, max_tokens=100)

    assert summary(recorded) == [
        (llm.PRIMARY_MODEL, "primary", "failed", "ReadTimeout"),
        (llm.FALLBACK_MODEL, "fallback", "ok", None),
    ]
    failed = recorded[0]
    assert (failed.prompt_tokens, failed.completion_tokens) == (None, None)
    assert failed.total_ms is None and failed.wall_ms >= 0
    assert failed.output_chars == 0 and not failed.complete


def test_both_failing_records_both_calls(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = [("cut", "length")]

    with pytest.raises(llm.ModelOutputError):
        llm.chat(MESSAGES, max_tokens=100)

    assert summary(recorded) == [
        (llm.PRIMARY_MODEL, "primary", "failed", "ConnectionError"),
        (llm.FALLBACK_MODEL, "fallback", "capped", None),
    ]


def test_an_error_that_is_not_the_models_is_recorded_then_raised(
    model_client, recorded
):
    model_client.replies[llm.PRIMARY_MODEL] = [ValueError("bad request")]

    with pytest.raises(ValueError):
        llm.chat(MESSAGES, max_tokens=100)

    assert summary(recorded) == [(llm.PRIMARY_MODEL, "primary", "failed", "ValueError")]


def test_the_fallback_only_path_is_one_fallback_call(model_client, recorded):
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("refused")]

    with pytest.raises(llm.ModelOutputError):
        llm.chat(MESSAGES, max_tokens=10, model_role="fallback")

    assert summary(recorded) == [
        (llm.FALLBACK_MODEL, "fallback", "failed", "ConnectionError")
    ]


def test_each_call_has_its_own_span_id(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [("cut", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = ["ok"]

    llm.chat(MESSAGES, max_tokens=10)

    assert len({c.id for c in recorded}) == 2


# -------------------------------------------------------------------
# Timings, request and sizes
# -------------------------------------------------------------------
def test_timings_are_converted_from_nanoseconds_to_ms(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [("reply", "stop", DONE)]

    llm.chat(MESSAGES, max_tokens=4096)

    (call,) = recorded
    timings = (call.total_ms, call.load_ms, call.prompt_eval_ms, call.eval_ms)
    assert timings == (212_400, 1_250, 9_877, 201_000)


def test_first_token_and_wall_times_are_measured(model_client, recorded, monkeypatch):
    # the start, the first chunk with text, then the end of the round trip
    ticks = iter([100.0, 102.5, 104.0])
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(ticks, 104.0))
    model_client.replies[llm.PRIMARY_MODEL] = ["0123456789"]  # two chunks

    llm.chat(MESSAGES, max_tokens=10)

    (call,) = recorded
    assert call.first_token_ms == 2500
    assert call.wall_ms == 4000
    assert call.ended_at >= call.started_at


def test_a_reply_with_no_text_has_no_first_token_time(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [""]

    llm.chat(MESSAGES, max_tokens=10)

    assert recorded[0].first_token_ms is None
    assert recorded[0].wall_ms >= 0


def test_the_request_options_are_recorded_but_not_the_prompt(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = ["{}"]

    llm.chat(MESSAGES, max_tokens=123, format={"type": "object"})

    assert recorded[0].request == {
        "temperature": 0,
        "presence_penalty": 0,
        "num_ctx": llm.NUM_CTX,
        "num_predict": 123,
        "think": False,
        "structured_output": True,
    }


def test_text_sizes_are_counted_in_characters(model_client, recorded):
    messages = [{"role": "system", "content": "abc"}, {"role": "user", "content": "de"}]
    model_client.replies[llm.PRIMARY_MODEL] = ["  reply  "]

    llm.chat(messages, max_tokens=10)

    assert (recorded[0].input_chars, recorded[0].output_chars) == (5, 9)


# -------------------------------------------------------------------
# Stop markers
# -------------------------------------------------------------------
def old_reply(chunks, marker):
    """What llm.chat returned before it read past the marker."""
    text = ""
    for chunk in chunks:
        text += chunk
        if marker in text:
            return text.split(marker)[0].strip()
    return text.strip()


@pytest.mark.parametrize(
    "reply",
    [
        "Page text\n[[END]]\nrambling on",
        "Page text [[EN",  # the marker never completes
        "[[END]]",
        "  a  \n\n[[END]] [[END]] b",
        "line 1\nline 2\n[[END]]\n",  # the marker spans two chunks
        "no marker at all",
    ],
)
def test_reading_past_the_marker_returns_the_same_text_as_before(model_client, reply):
    model_client.replies[llm.PRIMARY_MODEL] = [reply]
    chunks = [reply[i : i + 5] for i in range(0, len(reply), 5)]  # as the fake sends

    got = llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]")

    assert got == old_reply(chunks, "[[END]]")


def test_a_stop_marker_call_gets_its_token_counts(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [("Page\n[[END]]\nmore", "stop", DONE)]

    assert llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]") == "Page"

    (call,) = recorded
    assert call.status == "stopped"
    assert (call.prompt_tokens, call.completion_tokens) == (650, 1583)
    assert call.total_ms == 212_400


def test_the_stop_marker_is_sent_as_ollamas_stop_option(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = ["Page"]  # Ollama trimmed the marker

    assert llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]") == "Page"

    assert model_client.calls[0]["options"]["stop"] == ["[[END]]"]
    assert recorded[0].request["stop"] == ["[[END]]"]
    assert recorded[0].status == "stopped"


def test_the_stream_is_read_to_the_end_after_the_marker(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [("ab[[END]]" + "x" * 50, "stop")]

    llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]")

    assert recorded[0].output_chars == len("ab[[END]]") + 50


def test_a_marker_reply_that_ran_on_to_the_cap_is_still_complete(
    model_client, recorded
):
    model_client.replies[llm.PRIMARY_MODEL] = [("Page [[END]] on and on", "length")]

    assert llm.chat(MESSAGES, max_tokens=100, stop_marker="[[END]]") == "Page"
    assert recorded[0].status == "stopped" and recorded[0].complete


# -------------------------------------------------------------------
# Images
# -------------------------------------------------------------------
def pixmap(width, height):
    return fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)


def png(width, height) -> bytes:
    return pixmap(width, height).tobytes("png")


def jpeg(width, height) -> bytes:
    return pixmap(width, height).tobytes("jpeg")


def test_images_are_counted_with_their_sizes_and_never_kept(model_client, recorded):
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u", "images": [png(17, 22), jpeg(30, 12)]},
        {
            "role": "user",
            "content": "v",
            "images": [base64.b64encode(png(5, 6)).decode(), b"not an image"],
        },
    ]
    model_client.replies[llm.PRIMARY_MODEL] = ["ok"]

    llm.chat(messages, max_tokens=10)

    (call,) = recorded
    assert call.image_count == 4
    assert call.image_pixels == ((17, 22), (30, 12), (5, 6), None)
    assert call.input_chars == 3  # text only
    assert not any(isinstance(v, (bytes, bytearray)) for v in vars(call).values())


def test_image_size_reads_paths_and_ollama_images(tmp_path):
    path = tmp_path / "page.png"
    path.write_bytes(png(40, 50))

    assert llm.image_size(path) == (40, 50)
    assert llm.image_size(str(path)) == (40, 50)
    assert llm.image_size(ollama.Image(value=png(3, 4))) == (3, 4)
    assert llm.image_size(tmp_path / "missing.png") is None
    assert llm.image_size(b"") is None


# -------------------------------------------------------------------
# Truncation heuristic
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "input_tokens, expected",
    [(None, None), (100, False), (12_287, False), (12_288, True), (16_384, True)],
)
def test_prompt_truncated_when_the_prompt_leaves_less_than_num_predict(
    input_tokens, expected
):
    assert llm.prompt_truncated(input_tokens, 16_384, 4_096) is expected


def test_each_call_carries_the_truncation_check(model_client, recorded):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ("x", "stop", {"prompt_eval_count": llm.NUM_CTX - 10})
    ]

    llm.chat(MESSAGES, max_tokens=100)

    assert recorded[0].prompt_truncated is True


# -------------------------------------------------------------------
# Model digests
# -------------------------------------------------------------------
def test_the_digest_is_looked_up_once_per_model(model_client, recorded):
    model_client.digests = {llm.PRIMARY_MODEL: "abc123", "other:1b": "zzz"}
    model_client.replies[llm.PRIMARY_MODEL] = ["one", "two"]

    llm.chat(MESSAGES, max_tokens=10)
    llm.chat(MESSAGES, max_tokens=10)

    assert [c.model_digest for c in recorded] == ["abc123", "abc123"]
    assert model_client.list_calls == 1


def test_an_untagged_model_matches_its_latest_tag(model_client):
    model_client.digests = {"gemma:latest": "d1"}

    assert llm.model_digest("gemma") == "d1"


def test_a_failed_digest_lookup_never_fails_the_call(model_client, recorded):
    model_client.list_error = ConnectionError("refused")
    model_client.replies[llm.PRIMARY_MODEL] = ["still fine"]

    assert llm.chat(MESSAGES, max_tokens=10) == "still fine"
    assert recorded[0].model_digest is None
    assert recorded[0].status == "ok"


def test_a_model_missing_from_the_list_has_no_digest(model_client, recorded):
    model_client.digests = {"something-else": "x"}
    model_client.replies[llm.PRIMARY_MODEL] = ["ok"]

    llm.chat(MESSAGES, max_tokens=10)

    assert recorded[0].model_digest is None
