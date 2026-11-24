"""
Recordings known to be stale: units whose recorded replies no longer match
what the code asks the model, waiting for a live run to record them again
(scripts/evals/stale_recordings.json).

CI replays every other unit (`--known-stale`). The list can't drift: a unit
that misses and isn't on it fails the replay, and so does one on it that
replays again, so it is taken off. What is on it is not measured, and the
replay says so each time. See docs/adr/0009-measured-evals.md.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path

from dataset.build import DATASET_DIR
from evals.cassettes import CassetteMiss

STALE_FILE = DATASET_DIR.parent / "stale_recordings.json"


def load(kind: str, path: Path = STALE_FILE) -> dict[str, list[str]]:
    """The stale units of an eval ("e2e" or "injection"), by model."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))[kind]


def without(ids: list[str], stale: dict[str, list[str]], model: str) -> list[str]:
    """The units of `ids` to measure."""
    return [i for i in ids if i not in stale.get(model, ())]


def replaying(ids: list[str], replay: Callable[[str], object]) -> list[str]:
    """Those of the stale `ids` that `replay` answers from the recordings after
    all: they are no longer stale."""
    found = []
    for unit in ids:
        try:
            replay(unit)
        except CassetteMiss:
            continue
        found.append(unit)
    return found


def notice(kind: str, not_measured: dict[str, list[str]]) -> str:
    """One line naming what was not measured; "" for nothing. On GitHub it is a
    warning, which the run's page shows."""
    parts = [f"{m}: {', '.join(ids)}" for m, ids in not_measured.items() if ids]
    if not parts:
        return ""
    count = sum(len(ids) for ids in not_measured.values())
    text = (
        f"{count} {kind} units NOT MEASURED, their recordings are stale "
        f"(scripts/evals/stale_recordings.json): {'; '.join(parts)}"
    )
    return f"::warning::{text}" if os.environ.get("GITHUB_ACTIONS") else text
