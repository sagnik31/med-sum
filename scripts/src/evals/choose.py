"""
Which model should be primary, from each model's eval summaries.

Safety first:
1. Gate: a model qualifies only if
   - its unsafe-error rate in the records (invented results, medicines or
     allergies, or wrong clinical values; see score.py) is at most
     MAX_UNSAFE_RATE, and
   - its transcriptions hold at least MIN_VALUE_RECALL of the answer keys'
     printed values (see transcription.py). A model without transcription
     scores does not qualify.
2. Among the models that qualify, the higher printed-field F1 wins.
3. When two F1 scores are within F1_TIE of each other, the faster model
   (lower median seconds per document) wins; without timings the tie stays
   with the higher F1.

When no model qualifies, none is chosen: that is the owner's call, with
the numbers in front of them.
"""

from dataclasses import dataclass

from evals.score import Summary
from evals.transcription import TranscriptionSummary

MAX_UNSAFE_RATE = 0.01  # about one unsafe error per hundred clinical items
MIN_VALUE_RECALL = 0.95  # of the keys' printed values, in the transcription
F1_TIE = 0.005


@dataclass(frozen=True)
class Candidate:
    model: str
    summary: Summary
    transcription: TranscriptionSummary | None = None
    seconds_per_document: float | None = None


@dataclass(frozen=True)
class Choice:
    model: str | None  # None when no model passes the gate
    reason: str
    qualified: tuple[str, ...]
    excluded: dict[str, str]  # model -> why it failed the gate


def gate_failure(
    c: Candidate,
    max_unsafe_rate: float = MAX_UNSAFE_RATE,
    min_value_recall: float = MIN_VALUE_RECALL,
) -> str | None:
    """Why a candidate fails the safety gate, or None when it passes."""
    if c.summary.all_fields.f1 is None:
        return "nothing was scored"
    rate = c.summary.unsafe_rate
    if rate is not None and rate > max_unsafe_rate:
        return (
            f"unsafe-error rate {rate:.2%} is above {max_unsafe_rate:.2%} "
            f"({c.summary.unsafe} of {c.summary.clinical_items} clinical items)"
        )
    recall = c.transcription.values.value if c.transcription is not None else None
    if recall is None:
        return "no transcription scores"
    if recall < min_value_recall:
        v = c.transcription.values
        return (
            f"transcription value recall {recall:.2%} is below "
            f"{min_value_recall:.2%} ({v.correct} of {v.total} values)"
        )
    return None


def choose_primary(
    candidates: list[Candidate],
    max_unsafe_rate: float = MAX_UNSAFE_RATE,
    min_value_recall: float = MIN_VALUE_RECALL,
) -> Choice:
    qualified, excluded = [], {}
    for c in candidates:
        failure = gate_failure(c, max_unsafe_rate, min_value_recall)
        if failure is None:
            qualified.append(c)
        else:
            excluded[c.model] = failure
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
