"""
Estimated cost of a pipeline run per model, from its pipeline_model_calls
rows and a dated price table. Code only: no network, no provider API.

Local models are priced at 0: what they cost is time, which the baseline
views report (wall time, tokens per second). Cloud prices change, so each
entry carries the date it was copied, and the cloud entry below is a
placeholder for the owner to fill in from the provider's published price
list. A run's tokens only price a cloud model correctly if they were
counted by that model's tokenizer; to estimate a cloud model from local
runs, use the model-neutral sizes (characters, image pixels) in
pipeline_model_calls with that provider's own conversion.

Conventions: input_tokens include cached_input_tokens (the cached part is
priced at the cached rate, the rest at the input rate), and output_tokens
include any reasoning tokens, as most providers bill them as output. All
amounts are Decimal, never float.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    """Prices per million tokens, in `currency`, as of `as_of`."""

    as_of: date
    input: Decimal
    cached_input: Decimal
    output: Decimal
    currency: str = "USD"
    note: str = ""
    placeholder: bool = False  # not a real price: no estimate is made with it


LOCAL = "local model (Ollama): no money cost; its cost is time, see the views"

PRICES: dict[str, Price] = {
    "qwen3.5:4b": Price(date(2026, 11, 9), Decimal(0), Decimal(0), Decimal(0),
                        note=LOCAL),
    "gemma4:e2b": Price(date(2026, 11, 9), Decimal(0), Decimal(0), Decimal(0),
                        note=LOCAL),
    # PLACEHOLDER: replace the name with the cloud model's id and the zeros
    # with its published prices per million tokens, and set as_of to the
    # date they were read. Until then no cost is estimated for it.
    "PLACEHOLDER-cloud-model": Price(
        date(2026, 11, 9), Decimal(0), Decimal(0), Decimal(0),
        note="placeholder: fill in from the provider's price list",
        placeholder=True,
    ),
}  # fmt: skip


@dataclass(frozen=True)
class ModelCost:
    """One model's share of a run, and its estimated cost."""

    model: str
    calls: int
    uncounted_calls: int  # calls that didn't fail but reported no counts
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    price: Price | None  # None: the model isn't in the table
    cost: Decimal | None  # None: no price, or a placeholder price


def cost_of(
    price: Price, *, input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> Decimal:
    """What these tokens cost at `price`, unrounded."""
    if cached_input_tokens > input_tokens:
        raise ValueError("cached input tokens are part of the input tokens")
    uncached = input_tokens - cached_input_tokens
    return (
        uncached * price.input
        + cached_input_tokens * price.cached_input
        + output_tokens * price.output
    ) / MILLION


def estimate_run_cost(
    conn, run_id: str, prices: dict[str, Price] | None = None
) -> dict[str, ModelCost]:
    """The estimated cost of a run per model, from its call rows."""
    prices = PRICES if prices is None else prices
    with conn.cursor() as cur:
        cur.execute(
            "SELECT model, count(*),"
            " count(*) FILTER (WHERE status <> 'failed'"
            "  AND (input_tokens IS NULL OR output_tokens IS NULL)),"
            " coalesce(sum(input_tokens), 0), coalesce(sum(cached_input_tokens), 0),"
            " coalesce(sum(output_tokens), 0), coalesce(sum(reasoning_tokens), 0)"
            " FROM pipeline_model_calls WHERE run_id = %s"
            " GROUP BY model ORDER BY model",
            (run_id,),
        )
        rows = cur.fetchall()
    costs = {}
    for model, calls, uncounted, inp, cached, out, reasoning in rows:
        price = prices.get(model)
        cost = None
        if price is not None and not price.placeholder:
            cost = cost_of(
                price,
                input_tokens=inp,
                cached_input_tokens=cached,
                output_tokens=out,
            )
        costs[model] = ModelCost(
            model=model,
            calls=calls,
            uncounted_calls=uncounted,
            input_tokens=inp,
            cached_input_tokens=cached,
            output_tokens=out,
            reasoning_tokens=reasoning,
            price=price,
            cost=cost,
        )
    return costs
