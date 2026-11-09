"""
The tracer stores each model round trip of a step as a pipeline_model_calls
row, numbered within the step, and the step's token totals are the sums of
its rows.
"""

import contextvars
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg.rows
import pytest

import llm
from observability import start_run, traced
from test_observability_db import GRAPH_VERSION, MESSAGES, add_document

DONE = {
    "prompt_eval_count": 650,
    "eval_count": 1583,
    "total_duration": 212_400_000_000,
    "load_duration": 1_250_000_000,
    "prompt_eval_duration": 9_876_543_210,
    "eval_duration": 201_000_000_000,
}


@pytest.fixture
def document_id(db) -> str:
    return add_document(db)


def call_rows(db, run_id) -> list[dict]:
    with db.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT c.*, s.seq AS step_seq, s.node FROM pipeline_model_calls c"
            " JOIN pipeline_steps s ON s.id = c.step_id"
            " WHERE c.run_id = %s ORDER BY s.seq, c.seq",
            (run_id,),
        )
        return cur.fetchall()


def step_totals(db, run_id) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT s.prompt_tokens, s.completion_tokens,"
            " (SELECT sum(input_tokens) FROM pipeline_model_calls c"
            "   WHERE c.step_id = s.id),"
            " (SELECT sum(output_tokens) FROM pipeline_model_calls c"
            "   WHERE c.step_id = s.id)"
            " FROM pipeline_steps s WHERE s.run_id = %s ORDER BY s.seq",
            (run_id,),
        )
        return cur.fetchall()


def test_each_round_trip_is_a_row_of_its_step(db, document_id, model_client):
    model_client.digests = {llm.PRIMARY_MODEL: "sha-qwen"}
    model_client.replies[llm.PRIMARY_MODEL] = [
        ConnectionError("refused"),
        ("cut", "length", DONE),
    ]
    model_client.replies[llm.FALLBACK_MODEL] = [("whole", "stop", {"eval_count": 7})]

    @traced("parse")
    def parse(state):
        try:
            llm.chat(MESSAGES, max_tokens=4096)
        except llm.ModelOutputError:
            pass
        return {"text": llm.chat(MESSAGES, max_tokens=4096)}

    model_client.replies[llm.FALLBACK_MODEL].insert(0, ConnectionError("down"))
    with start_run(document_id, GRAPH_VERSION) as run:
        parse({})
        run.finish("verified")

    rows = call_rows(db, run.id)
    assert [(r["seq"], r["role"], r["status"], r["error_type"]) for r in rows] == [
        (1, "primary", "failed", "ConnectionError"),
        (2, "fallback", "failed", "ConnectionError"),
        (3, "primary", "capped", None),
        (4, "fallback", "ok", None),
    ]
    capped = rows[2]
    assert capped["model_digest"] == "sha-qwen"
    assert (capped["input_tokens"], capped["output_tokens"]) == (650, 1583)
    assert (capped["total_ms"], capped["load_ms"]) == (212_400, 1_250)
    assert (capped["prompt_eval_ms"], capped["eval_ms"]) == (9_877, 201_000)
    assert capped["request"]["num_predict"] == 4096
    assert capped["image_pixels"] == [] and capped["prompt_truncated"] is False
    assert rows[0]["input_tokens"] is None and rows[0]["wall_ms"] >= 0
    assert all(r["provider"] == "ollama" for r in rows)
    assert all(r["ended_at"] >= r["started_at"] for r in rows)


def test_step_totals_are_the_sums_of_the_call_rows(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ("cut", "length", {"prompt_eval_count": 100, "eval_count": 4096}),
        ("a", "stop", {"prompt_eval_count": 30, "eval_count": 3}),
    ]
    model_client.replies[llm.FALLBACK_MODEL] = [
        ("b", "stop", {"prompt_eval_count": 90, "eval_count": 300})
    ]

    @traced("parse")
    def parse(state):
        llm.chat(MESSAGES, max_tokens=4096)  # capped, then the fallback
        return {"text": llm.chat(MESSAGES, max_tokens=4096)}

    @traced("validate")
    def validate(state):
        return {}  # no model calls

    with start_run(document_id, GRAPH_VERSION) as run:
        parse({})
        validate({})
        run.finish("verified")

    assert step_totals(db, run.id) == [(220, 4399, 220, 4399), (None, None, None, None)]


def test_a_stop_marker_call_is_stored_with_its_counts(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("Page\n[[END]]", "stop", DONE)]

    @traced("transcribe", page=1)
    def transcribe(state):
        return {"text": llm.chat(MESSAGES, max_tokens=4096, stop_marker="[[END]]")}

    with start_run(document_id, GRAPH_VERSION) as run:
        assert transcribe({}) == {"text": "Page"}
        run.finish("verified")

    (row,) = call_rows(db, run.id)
    assert row["status"] == "stopped"
    assert (row["input_tokens"], row["output_tokens"]) == (650, 1583)
    assert row["request"]["stop"] == ["[[END]]"]
    assert step_totals(db, run.id) == [(650, 1583, 650, 1583)]


def test_a_failed_step_keeps_its_calls(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = [httpx_timeout()]

    @traced("parse")
    def parse(state):
        return {"text": llm.chat(MESSAGES, max_tokens=10)}

    with start_run(document_id, GRAPH_VERSION) as run:
        with pytest.raises(llm.ModelOutputError):
            parse({})
        run.finish("failed")

    rows = call_rows(db, run.id)
    assert [(r["role"], r["status"]) for r in rows] == [
        ("primary", "failed"),
        ("fallback", "failed"),
    ]


def httpx_timeout():
    import httpx

    return httpx.ReadTimeout("timed out")


def test_parallel_steps_number_their_calls_on_their_own(db, document_id, model_client):
    # Every primary reply is capped, so each chat makes two calls; the
    # fallback answers or fails depending on which thread gets which reply,
    # but each step makes exactly four calls either way.
    pages = range(1, 9)
    model_client.replies[llm.PRIMARY_MODEL] = [
        ("cut", "length", {"eval_count": 1}) for _ in range(2 * len(pages))
    ]
    model_client.replies[llm.FALLBACK_MODEL] = [
        ("page", "stop", {"eval_count": 2}) for _ in pages
    ] + [ConnectionError("down") for _ in pages]
    lock = threading.Lock()
    original_chat = model_client.chat

    def chat(**kwargs):
        with lock:  # the fake's queues aren't thread-safe
            reply = original_chat(**kwargs)
        return reply

    model_client.chat = chat

    @traced("transcribe", page=lambda s: s["page"])
    def transcribe(state):
        for _ in range(2):
            try:
                llm.chat(MESSAGES, max_tokens=10)
            except llm.ModelOutputError:
                pass
        return {}

    with start_run(document_id, GRAPH_VERSION) as run:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, transcribe, {"page": p})
                for p in pages
            ]
            for f in futures:
                f.result()
        run.finish("verified")

    rows = call_rows(db, run.id)
    by_step = {}
    for r in rows:
        by_step.setdefault(r["step_seq"], []).append(r["seq"])
    assert sorted(by_step) == list(pages)
    assert all(seqs == [1, 2, 3, 4] for seqs in by_step.values())
    assert len(rows) == 32
    for totals in step_totals(db, run.id):
        assert totals[1] == totals[3]
    assert sum(t[1] for t in step_totals(db, run.id)) == 16 + 2 * len(pages)


def test_calls_outside_a_step_store_nothing(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["fine"]

    with start_run(document_id, GRAPH_VERSION) as run:
        llm.chat(MESSAGES, max_tokens=10)  # in the run, but in no step
        run.finish("verified")

    assert call_rows(db, run.id) == []


def test_image_sizes_are_stored_as_pairs(db, document_id, model_client):
    import fitz

    page = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 34), False).tobytes("png")
    model_client.replies[llm.PRIMARY_MODEL] = ["ok"]

    @traced("transcribe", page=1)
    def transcribe(state):
        messages = [{"role": "user", "content": "x", "images": [page, b"??"]}]
        return {"text": llm.chat(messages, max_tokens=10)}

    with start_run(document_id, GRAPH_VERSION) as run:
        transcribe({})
        run.finish("verified")

    (row,) = call_rows(db, run.id)
    assert (row["image_count"], row["image_pixels"]) == (2, [[12, 34], None])


# -------------------------------------------------------------------
# Run totals
# -------------------------------------------------------------------
def two_steps_of_calls(model_client):
    """A transcribe whose primary is capped, then a parse the primary answers."""
    model_client.replies[llm.PRIMARY_MODEL] = [
        ("cut", "length", {"prompt_eval_count": 100, "eval_count": 50,
                           "total_duration": 2_000_000_000}),
        ("ok", "stop", {"prompt_eval_count": 30, "eval_count": 3,
                        "total_duration": 1_000_000_000}),
    ]  # fmt: skip
    model_client.replies[llm.FALLBACK_MODEL] = [
        ConnectionError("down"),
    ]

    @traced("transcribe", page=1)
    def transcribe(state):
        try:
            llm.chat(MESSAGES, max_tokens=50)
        except llm.ModelOutputError:
            pass
        return {}

    @traced("parse")
    def parse(state):
        return {"text": llm.chat(MESSAGES, max_tokens=50)}

    return transcribe, parse


EXPECTED_TOTALS = {
    "model_calls": 3,
    "fallback_calls": 1,
    "capped_calls": 1,
    "failed_calls": 1,
    "input_tokens": 130,
    "output_tokens": 53,
    "cached_input_tokens": None,  # Ollama doesn't report them
    "reasoning_tokens": None,
    "model_total_ms": 3000,
}


def without_wall(totals: dict) -> dict:
    return {k: v for k, v in totals.items() if k != "model_wall_ms"}


def test_a_run_ends_with_its_model_totals(db, document_id, model_client, json_log):
    transcribe, parse = two_steps_of_calls(model_client)

    with start_run(document_id, GRAPH_VERSION) as run:
        transcribe({})
        parse({})
        run.finish("verified")

    assert without_wall(run.model_totals) == EXPECTED_TOTALS
    assert run.model_totals["model_wall_ms"] >= 0
    with db.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT * FROM pipeline_run_model_totals WHERE run_id = %s", (run.id,)
        )
        view = cur.fetchone()
    assert view["document_id"] == uuid.UUID(document_id)
    assert view["end_state"] == "verified"
    assert {k: view[k] for k in EXPECTED_TOTALS} == EXPECTED_TOTALS

    lines = [json.loads(s) for s in json_log.getvalue().splitlines()]
    (ended,) = [e for e in lines if e.get("event") == "run_ended"]
    assert ended["input_tokens"] == 130 and ended["fallback_calls"] == 1
    assert ended["model_calls"] == 3 and "cached_input_tokens" not in ended


def test_a_run_that_fails_on_an_exception_has_its_totals_too(
    db, document_id, model_client
):
    transcribe, _ = two_steps_of_calls(model_client)

    with pytest.raises(RuntimeError):
        with start_run(document_id, GRAPH_VERSION) as run:
            transcribe({})
            raise RuntimeError("crash")

    assert run.end_state == "failed"
    assert run.model_totals["model_calls"] == 2
    assert run.model_totals["input_tokens"] == 100


def test_a_run_without_model_calls_has_zero_calls(db, document_id):
    with start_run(document_id, GRAPH_VERSION) as run:
        run.finish("needs_review")

    assert run.model_totals["model_calls"] == 0
    assert run.model_totals["input_tokens"] is None
    assert run.model_totals["model_wall_ms"] == 0
