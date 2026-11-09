"""
Which model should be primary, from each model's eval summary.

Safety first:
1. Gate: a model qualifies only if its unsafe-error rate (invented
   results, medicines or allergies, or wrong clinical values; see
   score.py) is at most MAX_UNSAFE_RATE.
2. Among the models that qualify, the higher printed-field F1 wins.
3. When two F1 scores are within F1_TIE of each other, the faster model
   (lower median seconds per document) wins; without timings the tie stays
   with the higher F1.

When no model qualifies, none is chosen: that is the owner's call, with
the numbers in front of them.
"""

from dataclasses import dataclass

from evals.score import Summary

MAX_UNSAFE_RATE = 0.01  # about one unsafe error per hundred clinical items
F1_TIE = 0.005


@dataclass(frozen=True)
class Candidate:
    model: str
    summary: Summary
    seconds_per_document: float | None = None


@dataclass(frozen=True)
class Choice:
    model: str | None  # None when no model passes the gate
    reason: str
    qualified: tuple[str, ...]
    excluded: dict[str, str]  # model -> why it failed the gate


def choose_primary(
    candidates: list[Candidate], max_unsafe_rate: float = MAX_UNSAFE_RATE
) -> Choice:
    qualified, excluded = [], {}
    for c in candidates:
        rate = c.summary.unsafe_rate
        if c.summary.all_fields.f1 is None:
            excluded[c.model] = "nothing was scored"
        elif rate is not None and rate > max_unsafe_rate:
            excluded[c.model] = (
                f"unsafe-error rate {rate:.2%} is above {max_unsafe_rate:.2%} "
                f"({c.summary.unsafe} of {c.summary.clinical_items} clinical items)"
            )
        else:
            qualified.append(c)
    names = tuple(c.model for c in qualified)
    if not qualified:
        return Choice(None, "no model passes the safety gate", names, excluded)

    qualified.sort(key=lambda c: -c.summary.all_fields.f1)
    best = qualified[0]
    if len(qualified) == 1:
        return Choice(best.model, "the only model that passes the safety gate",
                      names, excluded)  # fmt: skip
    runner = qualified[1]
    gap = best.summary.all_fields.f1 - runner.summary.all_fields.f1
    if gap > F1_TIE:
        return Choice(best.model, f"higher field F1 (by {gap:.3f})", names, excluded)
    fast = [c for c in (best, runner) if c.seconds_per_document is not None]
    if len(fast) == 2 and runner.seconds_per_document < best.seconds_per_document:
        return Choice(runner.model, "F1 tied; faster per document", names, excluded)
    if len(fast) == 2:
        return Choice(best.model, "F1 tied; faster per document", names, excluded)
    return Choice(best.model, "F1 tied, no timings; higher F1", names, excluded)
