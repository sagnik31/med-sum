"""
Counts with their uncertainty. The dataset is small (17 documents, about
a hundred clinical items), so a rate alone overstates what it shows: every
rate in a report carries its count and a Wilson score interval.
"""

from dataclasses import dataclass
from math import sqrt

# 95% two-sided.
Z = 1.959964


@dataclass(frozen=True)
class Interval:
    hits: int
    total: int
    low: float
    high: float

    @property
    def rate(self) -> float | None:
        return self.hits / self.total if self.total else None

    def text(self) -> str:
        """`hits/total (rate, 95% CI low-high)`, or `0/0` with no total."""
        if not self.total:
            return "0/0"
        return (
            f"{self.hits}/{self.total} ({_pct(self.rate)}, "
            f"95% CI {_pct(self.low)}-{_pct(self.high)})"
        )

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "total": self.total,
            "rate": self.rate,
            "low": self.low,
            "high": self.high,
        }


def wilson(hits: int, total: int, z: float = Z) -> Interval:
    """
    The Wilson score interval for hits out of total. Unlike rate +- error,
    it stays inside 0-1 and is still informative at 0 or total hits: 0 of
    104 gives an upper bound near 3.6%, not 0%.
    """
    if not 0 <= hits <= total:
        raise ValueError(f"need 0 <= hits <= total, got {hits} of {total}")
    if total == 0:
        return Interval(0, 0, 0.0, 1.0)
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return Interval(hits, total, max(0.0, centre - half), min(1.0, centre + half))


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"
