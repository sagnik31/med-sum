"""
The circuit breaker of model_health as llm.chat uses it, with a fake clock
and the fake model client: a model that keeps failing is skipped for a
cool-down, then probed; quality failures never count toward it.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import insights_service as service
import llm
import model_health
from db_rows import add_account
from observability import start_run
from pipeline import nodes

PRIMARY, FALLBACK = llm.PRIMARY_MODEL, llm.FALLBACK_MODEL
MESSAGES = [{"role": "user", "content": "hello"}]
DOWN = ConnectionError("connection refused")


@pytest.fixture
def clock(monkeypatch):
    """A clock the test moves by hand."""
    t = {"now": 1000.0}
    monkeypatch.setattr(model_health, "now", lambda: t["now"])

    def advance(seconds):
        t["now"] += seconds

    return advance


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(model_health, "FAILURES", 3)
    monkeypatch.setattr(model_health, "COOLDOWN_S", 900)


def calls_to(model_client, model):
    return sum(1 for c in model_client.calls if c["model"] == model)


def state_of(model):
    return model_health.snapshot()["models"][model]["state"]


def fail_primary(model_client, times):
    """`times` chats whose primary is down and whose fallback answers."""
    model_client.replies[PRIMARY] = [DOWN] * times
    model_client.replies[FALLBACK] = ["from the fallback"] * times
    for _ in range(times):
        assert llm.chat(MESSAGES, max_tokens=10) == "from the fallback"


# -------------------------------------------------------------------
# Opening
# -------------------------------------------------------------------
def test_a_model_opens_after_the_set_number_of_failures_in_a_row(model_client, clock):
    fail_primary(model_client, 2)
    assert state_of(PRIMARY) == "closed"

    fail_primary(model_client, 1)

    assert state_of(PRIMARY) == "open"
    assert model_health.snapshot()["models"][PRIMARY]["last_error_type"] == (
        "ConnectionError"
    )


def test_a_success_resets_the_count(model_client, clock):
    fail_primary(model_client, 2)
    model_client.replies[PRIMARY] = ["fine"]
    llm.chat(MESSAGES, max_tokens=10)
    fail_primary(model_client, 2)

    assert state_of(PRIMARY) == "closed"
    assert model_health.snapshot()["models"][PRIMARY]["consecutive_failures"] == 2


@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("timed out"), httpx.ConnectError("refused")],
    ids=["timeout", "refused"],
)
def test_timeouts_and_refused_connections_count(model_client, clock, error):
    model_client.replies[PRIMARY] = [error] * 3
    model_client.replies[FALLBACK] = ["ok"] * 3
    for _ in range(3):
        llm.chat(MESSAGES, max_tokens=10)

    assert state_of(PRIMARY) == "open"


def test_a_capped_reply_is_not_a_failure(model_client, clock):
    # The model answered; that the content is too long is not an outage.
    model_client.replies[PRIMARY] = [("cut off", "length")] * 4
    model_client.replies[FALLBACK] = ["complete"] * 4
    for _ in range(4):
        llm.chat(MESSAGES, max_tokens=10)

    assert state_of(PRIMARY) == "closed"


def test_a_bug_in_the_request_is_not_a_failure(model_client, clock):
    model_client.replies[PRIMARY] = [TypeError("bad argument")] * 4
    for _ in range(4):
        with pytest.raises(TypeError):
            llm.chat(MESSAGES, max_tokens=10)

    assert state_of(PRIMARY) == "closed"


# -------------------------------------------------------------------
# While open
# -------------------------------------------------------------------
def test_an_open_primary_is_skipped_and_the_fallback_answers(model_client, clock):
    fail_primary(model_client, 3)
    model_client.replies[PRIMARY] = ["would answer"]
    model_client.replies[FALLBACK] = ["from the fallback"]
    before = calls_to(model_client, PRIMARY)

    assert llm.chat(MESSAGES, max_tokens=10) == "from the fallback"
    assert calls_to(model_client, PRIMARY) == before  # not called at all


def test_with_both_open_a_call_fails_at_once(model_client, clock):
    model_client.replies[PRIMARY] = [DOWN] * 3
    model_client.replies[FALLBACK] = [DOWN] * 3
    for _ in range(3):
        with pytest.raises(llm.ModelOutputError):
            llm.chat(MESSAGES, max_tokens=10)
    calls = len(model_client.calls)

    with pytest.raises(llm.ModelUnavailableError, match="No model available"):
        llm.chat(MESSAGES, max_tokens=10)
    assert len(model_client.calls) == calls


def test_an_open_fallback_fails_a_fallback_only_call_at_once(model_client, clock):
    model_client.replies[FALLBACK] = [DOWN] * 3
    for _ in range(3):
        with pytest.raises(llm.ModelOutputError):
            llm.chat(MESSAGES, max_tokens=10, model_role="fallback")

    with pytest.raises(llm.ModelUnavailableError, match="next try in 900 s"):
        llm.chat(MESSAGES, max_tokens=10, model_role="fallback")


def test_a_model_unavailable_error_is_a_model_output_error():
    # So a job ends failed with its message, like every model being down.
    assert issubclass(llm.ModelUnavailableError, llm.ModelOutputError)


# -------------------------------------------------------------------
# Probing after the cool-down
# -------------------------------------------------------------------
def test_after_the_cool_down_one_call_probes_and_a_success_closes_it(
    model_client, clock
):
    fail_primary(model_client, 3)
    clock(899)
    model_client.replies[FALLBACK] = ["from the fallback"]
    llm.chat(MESSAGES, max_tokens=10)
    assert state_of(PRIMARY) == "open"  # not yet

    clock(1)
    model_client.replies[PRIMARY] = ["back"]

    assert llm.chat(MESSAGES, max_tokens=10) == "back"
    assert state_of(PRIMARY) == "closed"


@pytest.fixture
def run(db):
    """An open pipeline run on a fresh documents row."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, 'a.pdf', 'application/pdf', '/x') RETURNING id",
            (add_account(db),),
        )
        document_id = str(cur.fetchone()[0])
    with start_run(document_id, "test-breaker") as r:
        yield r
        if not r.ended:
            r.finish("verified")


def test_a_run_notes_each_switch_made_during_its_calls(model_client, clock, run):
    fail_primary(model_client, 3)
    clock(900)
    model_client.replies[PRIMARY] = ["back"]
    llm.chat(MESSAGES, max_tokens=10)

    assert run.breaker_switches == [
        {"event": "model_disabled", "model": PRIMARY, "model_role": "primary"},
        {"event": "model_restored", "model": PRIMARY, "model_role": "primary"},
    ]


def test_a_run_notes_no_switch_when_the_breaker_stays_as_it_was(
    model_client, clock, run
):
    fail_primary(model_client, 2)  # not yet open
    model_client.replies[PRIMARY] = ["fine"]
    llm.chat(MESSAGES, max_tokens=10)

    assert run.breaker_switches == []


def test_a_switch_outside_a_run_is_noted_nowhere(model_client, clock):
    fail_primary(model_client, 3)  # no run open: nothing to note it on

    assert state_of(PRIMARY) == "open"


def test_a_failed_probe_opens_it_for_another_cool_down(model_client, clock):
    fail_primary(model_client, 3)
    clock(900)
    fail_primary(model_client, 1)  # the probe

    assert state_of(PRIMARY) == "open"
    assert model_health.retry_in(PRIMARY) == 900
    before = calls_to(model_client, PRIMARY)
    model_client.replies[FALLBACK] = ["from the fallback"]
    llm.chat(MESSAGES, max_tokens=10)
    assert calls_to(model_client, PRIMARY) == before


def test_only_one_probe_at_a_time(clock):
    for _ in range(3):
        model_health.failed(PRIMARY, "primary", DOWN)
    clock(900)

    assert model_health.allow(PRIMARY, "primary") is True  # the probe
    assert model_health.allow(PRIMARY, "primary") is False  # still in flight
    model_health.abandoned(PRIMARY)  # the probe raised a bug
    assert model_health.allow(PRIMARY, "primary") is True


# -------------------------------------------------------------------
# Quality failures
# -------------------------------------------------------------------
def test_quality_failures_are_counted_and_never_open_the_breaker(clock):
    for _ in range(5):
        model_health.quality_failure(PRIMARY, "primary", "off_schema")
    model_health.quality_failure(PRIMARY, "primary", "validation")

    health = model_health.snapshot()["models"][PRIMARY]
    assert health["state"] == "closed"
    assert health["quality_failures"] == {"off_schema": 5, "validation": 1}


def test_a_parse_off_its_schema_is_a_quality_failure(model_client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        model_health, "quality_failure", lambda m, r, kind: seen.append((m, kind))
    )
    model_client.replies[PRIMARY] = ["not json"]

    class Step:
        model, model_role = PRIMARY, "primary"

        def fail(self, error):
            pass

        def set_output(self, output):
            pass

        def model_call_list(self):
            return []

    monkeypatch.setattr(nodes, "_step", lambda: Step())
    state = {"doc_type": nodes.DocumentType.LAB_REPORT, "parse_attempts": 0}
    part = nodes.ParsePart(index=0, page=1, first_line=1, last_line=1)
    result = nodes._part_call(state, part, MESSAGES)

    assert result.parse is None
    assert seen == [(PRIMARY, "off_schema")]


# -------------------------------------------------------------------
# Logs and the health endpoint
# -------------------------------------------------------------------
def test_opening_probing_and_restoring_are_logged(model_client, clock, json_log):
    fail_primary(model_client, 3)
    clock(900)
    model_client.replies[PRIMARY] = ["back"]
    llm.chat(MESSAGES, max_tokens=10)

    lines = [json.loads(s) for s in json_log.getvalue().splitlines()]
    events = [x for x in lines if x.get("event", "").startswith("model_")
              and x["event"] in ("model_disabled", "model_probe", "model_restored")]  # fmt: skip
    assert [e["event"] for e in events] == [
        "model_disabled", "model_probe", "model_restored",
    ]  # fmt: skip
    disabled = events[0]
    assert (disabled["model"], disabled["model_role"]) == (PRIMARY, "primary")
    assert (disabled["failures"], disabled["error_type"]) == (3, "ConnectionError")
    assert disabled["cooldown_s"] == 900
    assert "connection refused" not in json_log.getvalue()  # type only


def test_the_health_endpoint_shows_each_models_breaker(model_client, clock):
    fail_primary(model_client, 3)

    body = TestClient(service.app).get("/health").json()

    primary = body["model_health"]["models"][PRIMARY]
    assert (primary["state"], primary["retry_in_s"]) == ("open", 900)
    assert body["model_health"]["models"][FALLBACK]["state"] == "closed"
