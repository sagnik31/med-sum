"""
The model-call views in db/schema.sql (per run, per document, per node and
model, per model) return the numbers worked out by hand from rows built
here directly, without the tracer.
"""

import uuid
from decimal import Decimal

import psycopg.rows
import pytest

from db_rows import add_account

Q, G = "qwen3.5:4b", "gemma4:e2b"

# (run, step seq, node, call seq, model, role, status, input tokens,
#  output tokens, wall ms, eval ms, prompt eval ms, total ms)
CALLS = [
    (1, 1, "transcribe", 1, Q, "primary", "ok", 100, 50, 2000, 1000, 500, 1800),
    (1, 2, "transcribe", 1, Q, "primary", "capped", 300, 400, 6000, 4000, 1000, 5500),
    (1, 2, "transcribe", 2, G, "fallback", "ok", 200, 100, 1000, 500, 250, 900),
    (1, 3, "parse", 1, Q, "primary", "failed", None, None, 600000, None, None, None),
    (1, 3, "parse", 2, G, "fallback", "ok", 50, 20, 400, 200, 100, 350),
    (2, 1, "parse", 1, Q, "primary", "ok", 60, 30, 3000, 2000, 500, 2900),
]


@pytest.fixture
def calls(db) -> dict:
    """The rows of CALLS for one document over two runs; returns the ids."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type,"
            " storage_path) VALUES (%s, 'a.pdf', 'application/pdf', '/x')"
            " RETURNING id",
            (add_account(db),),
        )
        (document,) = cur.fetchone()
        runs, steps = {}, {}
        for n in (1, 2):
            cur.execute(
                "INSERT INTO pipeline_runs (document_id, graph_version, started_at)"
                " VALUES (%s, 'v1', now() - %s * interval '1 hour') RETURNING id",
                (document, 3 - n),
            )
            (runs[n],) = cur.fetchone()
        for run, seq, node, *_ in CALLS:
            if (run, seq) not in steps:
                cur.execute(
                    "INSERT INTO pipeline_steps (run_id, seq, node) VALUES (%s, %s, %s)"
                    " RETURNING id",
                    (runs[run], seq, node),
                )
                (steps[run, seq],) = cur.fetchone()
        for (
            run,
            seq,
            _,
            call_seq,
            model,
            role,
            status,
            i,
            o,
            wall,
            ev,
            pe,
            tot,
        ) in CALLS:
            cur.execute(
                "INSERT INTO pipeline_model_calls (id, step_id, run_id, seq,"
                " started_at, ended_at, model, role, status, error_type,"
                " input_tokens, output_tokens, wall_ms, eval_ms, prompt_eval_ms,"
                " total_ms, input_chars, output_chars)"
                " VALUES (%s, %s, %s, %s, now(), now(), %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, 10, 5)",
                (
                    uuid.uuid4(), steps[run, seq], runs[run], call_seq, model, role,
                    status, "ReadTimeout" if status == "failed" else None, i, o,
                    wall, ev, pe, tot,
                ),
            )  # fmt: skip
    return {"document": document, "runs": runs}


def rows(db, sql, *params) -> list[dict]:
    with db.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


TOTAL_COLUMNS = (
    "model_calls", "fallback_calls", "capped_calls", "failed_calls",
    "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens",
    "model_wall_ms", "model_total_ms",
)  # fmt: skip


def pick(row, names):
    return tuple(row[n] for n in names)


def test_totals_per_run(db, calls):
    by_run = {
        r["run_id"]: r for r in rows(db, "SELECT * FROM pipeline_run_model_totals")
    }

    assert pick(by_run[calls["runs"][1]], TOTAL_COLUMNS) == (
        5, 2, 1, 1, 650, 570, None, None, 609_400, 8_550
    )  # fmt: skip
    assert pick(by_run[calls["runs"][2]], TOTAL_COLUMNS) == (
        1, 0, 0, 0, 60, 30, None, None, 3_000, 2_900
    )  # fmt: skip
    assert all(r["document_id"] == calls["document"] for r in by_run.values())


def test_totals_per_document_add_up_its_runs(db, calls):
    (doc,) = rows(db, "SELECT * FROM pipeline_document_model_totals")

    assert doc["document_id"] == calls["document"] and doc["runs"] == 2
    assert pick(doc, TOTAL_COLUMNS) == (
        6, 2, 1, 1, 710, 600, None, None, 612_400, 11_450
    )  # fmt: skip


def test_baseline_per_node_and_model(db, calls):
    found = {
        (r["node"], r["model"]): r
        for r in rows(db, "SELECT * FROM pipeline_node_model_baseline")
    }

    assert set(found) == {("transcribe", Q), ("transcribe", G), ("parse", Q),
                          ("parse", G)}  # fmt: skip
    t = found["transcribe", Q]
    assert (t["calls"], t["failed_calls"]) == (2, 0)
    assert (t["avg_input_tokens"], t["p50_input_tokens"]) == (200, 200)
    assert t["p95_input_tokens"] == pytest.approx(290)  # 100 + 0.95 * 200
    assert (t["avg_output_tokens"], t["p50_output_tokens"]) == (225, 225)
    assert t["p95_output_tokens"] == pytest.approx(382.5)
    assert (t["avg_wall_ms"], t["p50_wall_ms"]) == (4000, 4000)
    assert t["p95_wall_ms"] == pytest.approx(5800)

    # The parse primary's timeout counts as a call but not in its times.
    p = found["parse", Q]
    assert (p["calls"], p["failed_calls"]) == (2, 1)
    assert p["avg_input_tokens"] == 60 and p["p95_input_tokens"] == 60
    assert (p["avg_wall_ms"], p["p95_wall_ms"]) == (3000, 3000)
    assert found["parse", G]["avg_wall_ms"] == 400


def test_baseline_per_model(db, calls):
    found = {r["model"]: r for r in rows(db, "SELECT * FROM pipeline_model_baseline")}

    q = found[Q]
    assert (q["calls"], q["primary_calls"], q["fallback_calls"]) == (4, 4, 0)
    assert q["handed_to_fallback"] == 2
    assert q["fallback_rate"] == Decimal("0.5000")
    assert (q["cap_hit_rate"], q["failure_rate"]) == (Decimal("0.2500"),) * 2
    assert q["output_tokens_per_s"] == Decimal("68.57")  # 480 tokens / 7 s
    assert q["input_tokens_per_s"] == Decimal("230.00")  # 460 tokens / 2 s
    assert (q["input_tokens"], q["output_tokens"], q["wall_ms"]) == (460, 480, 611_000)

    g = found[G]
    assert (g["calls"], g["primary_calls"], g["fallback_calls"]) == (2, 0, 2)
    assert g["fallback_rate"] is None  # never the primary
    assert (g["cap_hit_rate"], g["failure_rate"]) == (Decimal("0"),) * 2
    assert g["output_tokens_per_s"] == Decimal("171.43")  # 120 / 0.7 s
    assert g["input_tokens_per_s"] == Decimal("714.29")  # 250 / 0.35 s


def test_the_views_are_empty_without_calls(db):
    for view in (
        "pipeline_document_model_totals",
        "pipeline_node_model_baseline",
        "pipeline_model_baseline",
    ):
        assert rows(db, f"SELECT * FROM {view}") == [], view
