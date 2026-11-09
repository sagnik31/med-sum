"""The cost estimate: Decimal arithmetic, the price table, and a run's rows."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from db_rows import add_account
from observability.cost import PRICES, Price, cost_of, estimate_run_cost

CLOUD = Price(date(2026, 1, 1), Decimal("0.30"), Decimal("0.075"), Decimal("2.50"))


def test_cost_is_per_million_tokens_with_the_cached_part_cheaper():
    cost = cost_of(
        CLOUD, input_tokens=1_000_000, cached_input_tokens=200_000, output_tokens=10
    )

    # 800k * 0.30 + 200k * 0.075 + 10 * 2.50, per million
    assert cost == Decimal("0.24") + Decimal("0.015") + Decimal("0.000025")
    assert isinstance(cost, Decimal)


def test_cached_tokens_cannot_exceed_the_input():
    with pytest.raises(ValueError):
        cost_of(CLOUD, input_tokens=1, cached_input_tokens=2, output_tokens=0)


def test_the_table_prices_local_models_at_zero_and_marks_the_placeholder():
    assert PRICES["qwen3.5:4b"].input == 0 and "time" in PRICES["qwen3.5:4b"].note
    assert PRICES["gemma4:e2b"].output == 0
    placeholders = [m for m, p in PRICES.items() if p.placeholder]
    assert placeholders == ["PLACEHOLDER-cloud-model"]
    for price in PRICES.values():
        assert isinstance(price.as_of, date)
        assert all(
            isinstance(v, Decimal)
            for v in (price.input, price.cached_input, price.output)
        )


@pytest.fixture
def run_id(db) -> str:
    """A run with calls by a local model, a priced cloud model, a placeholder
    and an unpriced model."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type,"
            " storage_path) VALUES (%s, 'a.pdf', 'application/pdf', '/x')"
            " RETURNING id",
            (add_account(db),),
        )
        (document,) = cur.fetchone()
        cur.execute(
            "INSERT INTO pipeline_runs (document_id, graph_version)"
            " VALUES (%s, 'v1') RETURNING id",
            (document,),
        )
        (run,) = cur.fetchone()
        cur.execute(
            "INSERT INTO pipeline_steps (run_id, seq, node) VALUES (%s, 1, 'parse')"
            " RETURNING id",
            (run,),
        )
        (step,) = cur.fetchone()
        calls = [
            # model, role, status, input, cached, output, reasoning
            ("qwen3.5:4b", "primary", "failed", None, None, None, None),
            ("qwen3.5:4b", "primary", "ok", 650, None, 1583, None),
            ("cloud-x", "fallback", "ok", 1_000_000, 200_000, 9_990, 10),
            ("cloud-x", "fallback", "ok", None, None, None, None),  # no counts
            ("PLACEHOLDER-cloud-model", "fallback", "ok", 10, None, 10, None),
            ("unknown:1b", "fallback", "ok", 10, None, 10, None),
        ]
        for seq, (model, role, status, i, c, o, r) in enumerate(calls, start=1):
            cur.execute(
                "INSERT INTO pipeline_model_calls (id, step_id, run_id, seq,"
                " started_at, ended_at, model, role, status, error_type,"
                " input_tokens, cached_input_tokens, output_tokens,"
                " reasoning_tokens, wall_ms, input_chars, output_chars)"
                " VALUES (%s, %s, %s, %s, now(), now(), %s, %s, %s, %s, %s, %s, %s,"
                " %s, 1, 1, 1)",
                (uuid.uuid4(), step, run, seq, model, role, status,
                 "ReadTimeout" if status == "failed" else None, i, c, o, r),
            )  # fmt: skip
    return str(run)


def test_a_runs_cost_per_model(db, run_id):
    prices = {**PRICES, "cloud-x": CLOUD}

    costs = estimate_run_cost(db, run_id, prices)

    assert set(costs) == {"qwen3.5:4b", "cloud-x", "PLACEHOLDER-cloud-model",
                          "unknown:1b"}  # fmt: skip
    local = costs["qwen3.5:4b"]
    assert (local.calls, local.uncounted_calls) == (2, 0)  # a failure has no counts
    assert (local.input_tokens, local.output_tokens) == (650, 1583)
    assert local.cost == Decimal(0)

    cloud = costs["cloud-x"]
    assert (cloud.calls, cloud.uncounted_calls) == (2, 1)
    assert (cloud.cached_input_tokens, cloud.reasoning_tokens) == (200_000, 10)
    assert cloud.cost == Decimal("0.24") + Decimal("0.015") + Decimal("0.024975")

    assert costs["PLACEHOLDER-cloud-model"].cost is None  # never a fake price
    assert costs["unknown:1b"].price is None and costs["unknown:1b"].cost is None


def test_a_run_without_calls_costs_nothing(db, run_id):
    with db.cursor() as cur:
        cur.execute("DELETE FROM pipeline_model_calls")

    assert estimate_run_cost(db, run_id) == {}
