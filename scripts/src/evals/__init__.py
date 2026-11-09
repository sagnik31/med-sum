"""
Evals: scoring the pipeline's records against the synthetic dataset's
answer keys (`scripts/evals/dataset`). Scoring is pure code; nothing here
calls a model.
"""

from evals.choose import Candidate, Choice, choose_primary
from evals.score import (
    Counts,
    DocumentScore,
    Ratio,
    Summary,
    score_document,
    summarise,
)

__all__ = [
    "Candidate",
    "Choice",
    "choose_primary",
    "Counts",
    "DocumentScore",
    "Ratio",
    "Summary",
    "score_document",
    "summarise",
]
