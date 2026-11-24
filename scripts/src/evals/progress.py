"""
Checkpointed, resumable eval runs.

A live eval suite takes hours on a CPU, so each one saves its progress
after every unit of work: one (model, document) for evals.run and
evals.injection, one (model, variant, case) for evals.repair, one question
for evals.mcp_eval. A run is named on its first start (its label) and gets
a working directory, scripts/evals/results/<name>/progress/ (gitignored):

- run.json, the fingerprint: the code commit and whether the tree had
  changes (eval outputs aside), the prompt hashes, the installed model
  digests, the generation options (seed included), the dataset manifest's
  hash, and the models and units the run is made of;
- one file per finished unit, written atomically (a temporary file, then
  os.replace) right after the unit's cassette is saved.

`--resume <name>` loads run.json and refuses, naming what differs, when the
fingerprint doesn't match the current code, prompts, digests, options or
selection: a resumed run must be one run. It then skips the finished units
and runs the rest in the original order, and the report is built from all
of the unit files, as it is after an uninterrupted run. Without --resume, a
progress directory that already exists for the name is refused, never
overwritten. Once the report is written the progress directory is removed:
everything it held is in the report.

A crash loses only the unit in flight: its cassette is never saved, and the
eval database is fresh on each start, which is safe because each unit
inserts its own document.

Infrastructure errors stop the run instead of being scored: both models
down or unreachable (llm.ModelUnavailableError, llm.ModelsDownError), a
lost database connection, a full disk. Only an error from the document
itself counts as a result; otherwise an Ollama crash at 3 a.m. would fill
the rest of the report with misses.
"""

import errno
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import psycopg

import llm
from dataset.build import DATASET_DIR

RESULTS_DIR = DATASET_DIR.parent / "results"
PROGRESS_VERSION = 1

# What an eval writes inside the repository: never a change to the code a
# run is made with, so the fingerprint leaves it out (a live run's cassettes
# would otherwise make the tree look changed between a start and a resume).
OUTPUT_PATHS = (
    "scripts/evals/cassettes/",
    "scripts/evals/reports/",
    "scripts/evals/history/",
    "scripts/evals/results/",
)

_SRC_DIR = Path(__file__).resolve().parents[1]


class RunStopped(RuntimeError):
    """An infrastructure error stopped the run; finished units are kept."""


class ResumeRefused(RuntimeError):
    """The named run can't be started or resumed as asked."""


# -------------------------------------------------------------------
# Infrastructure errors
# -------------------------------------------------------------------
_DISK_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}


def _chain(error: BaseException) -> Iterable[BaseException]:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def is_infrastructure(error: BaseException) -> bool:
    """
    Whether an error says the machine, not the document, failed: every model
    down or skipped, the database connection lost, the disk full. Looks
    through the error's causes, since the graph may wrap one.
    """
    for e in _chain(error):
        if isinstance(e, (llm.ModelUnavailableError, llm.ModelsDownError)):
            return True
        if isinstance(e, psycopg.OperationalError):
            return True
        if isinstance(e, OSError) and e.errno in _DISK_FULL:
            return True
    return False


def stop_if_infrastructure(error: BaseException) -> None:
    """Raise RunStopped from `error` when it is an infrastructure error."""
    if is_infrastructure(error):
        raise RunStopped(f"{type(error).__name__}: {error}") from error


# -------------------------------------------------------------------
# The fingerprint
# -------------------------------------------------------------------
def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=_SRC_DIR, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def code_fingerprint() -> dict | None:
    """
    The commit, whether the tree has changes outside the eval outputs, and a
    hash of those changes (the diff and the untracked file names), so a
    change made between a start and a resume is seen even on a dirty tree.
    """
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=all")
    if commit is None or status is None:
        return None
    root = (_git("rev-parse", "--show-toplevel") or "").strip()
    excludes = [f":(exclude){p}" for p in OUTPUT_PATHS]
    diff = _git("-C", root, "diff", "HEAD", "--", ".", *excludes) if root else None
    changed = sorted(
        line
        for line in status.splitlines()
        if line[3:] and not line[3:].strip('"').startswith(OUTPUT_PATHS)
    )
    digest = hashlib.sha256(
        ((diff or "") + "\n".join(changed)).encode("utf-8")
    ).hexdigest()
    return {"commit": commit.strip(), "dirty": bool(changed), "changes": digest}


def json_hash(value) -> str:
    """The SHA-256 of a value as canonical JSON (a manifest, a case list)."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def installed_digests(client, models: Iterable[str]) -> dict[str, str | None]:
    """The digest of each model as the given (live) client lists it."""
    try:
        listed = {m.model: m.digest for m in client.list().models}
    except Exception:
        listed = {}
    return {m: listed.get(m) or listed.get(f"{m}:latest") for m in sorted(set(models))}


def fingerprint(
    *, kind: str, models: list[str], units: list[str], dataset: str, digests: dict,
    selection: dict | None = None,
) -> dict:  # fmt: skip
    """What a run is made with. Two starts may form one run only when equal."""
    from evals.history import prompt_hashes

    return {
        "progress_version": PROGRESS_VERSION,
        "kind": kind,
        "code": code_fingerprint(),
        "prompts": prompt_hashes(),
        "digests": dict(sorted(digests.items())),
        "generation_options": dict(llm.GENERATION_OPTIONS),
        "dataset": dataset,
        "models": list(models),
        "selection": selection or {},
        "units": list(units),
    }


_PARTS = {
    "code": "the code (commit or uncommitted changes)",
    "prompts": "the prompts",
    "digests": "the model digests (a model was pulled again)",
    "generation_options": "the generation options",
    "dataset": "the dataset",
    "models": "the models",
    "selection": "the selection (documents, split, subset or variant)",
    "units": "the units to run",
    "kind": "the kind of eval",
    "progress_version": "the progress format",
}


def differences(old: dict, new: dict) -> list[str]:
    """What differs between two fingerprints, in words."""
    out = []
    for part, words in _PARTS.items():
        if old.get(part) != new.get(part):
            if part == "prompts":
                a, b = old.get(part) or {}, new.get(part) or {}
                names = sorted(n for n in set(a) | set(b) if a.get(n) != b.get(n))
                words += f" ({', '.join(names)})"
            out.append(words)
    return out


# -------------------------------------------------------------------
# The progress directory
# -------------------------------------------------------------------
def write_atomic(path: Path, data) -> None:
    """Write JSON to `path` through a temporary file and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")  # fmt: skip
    os.replace(tmp, path)


def _slug(unit: str) -> str:
    return "".join(c if c.isalnum() or c in "-." else "_" for c in unit)


def unit_id(*parts: str) -> str:
    return "/".join(parts)


class Progress:
    """
    The progress directory of one named run. `start` refuses an existing
    run, `resume` one whose fingerprint differs; `save` keeps a finished
    unit; `load` gives a finished unit's data, or None.
    """

    def __init__(self, directory: Path, run: dict, *, out: Callable = print):
        self.directory, self.run, self.out = directory, run, out
        self.units: list[str] = run["units"]
        self.started = time.monotonic()
        self.seconds: list[float] = []  # units run by this process

    @classmethod
    def open(
        cls, name: str, run: dict, *, resume: bool, root: Path | None = None,
        out: Callable = print,
    ) -> "Progress":  # fmt: skip
        directory = (root or RESULTS_DIR) / name / "progress"
        path = directory / "run.json"
        if resume:
            if not path.exists():
                raise ResumeRefused(f"no run named {name!r} to resume ({directory})")
            old = json.loads(path.read_text(encoding="utf-8"))
            changed = differences(old, run)
            if changed:
                raise ResumeRefused(
                    f"run {name!r} can't be resumed: since it started, "
                    + "; ".join(changed)
                    + " changed. Start a new run under another --label."
                )
        else:
            if directory.exists() and any(directory.iterdir()):
                raise ResumeRefused(
                    f"a run named {name!r} already has progress in {directory}; "
                    "pass --resume to carry on, or choose another --label"
                )
            write_atomic(path, run)
        progress = cls(directory, run, out=out)
        done = len(progress.finished())
        if resume:
            out(f"resuming {name}: {done} of {len(progress.units)} units done")
        return progress

    def _path(self, unit: str) -> Path:
        # The hash keeps two units whose names differ only in case, or in
        # characters a file name can't hold, in two files.
        digest = hashlib.sha256(unit.encode("utf-8")).hexdigest()[:8]
        return self.directory / "units" / f"{_slug(unit)}-{digest}.json"

    def load(self, unit: str) -> dict | None:
        path = self._path(unit)
        if not path.exists():
            return None
        kept = json.loads(path.read_text(encoding="utf-8"))
        return kept["data"] if kept["unit"] == unit else None

    def finished(self) -> list[str]:
        return [u for u in self.units if self.load(u) is not None]

    def finish(self) -> None:
        """The report is written: the progress is no longer needed."""
        shutil.rmtree(self.directory)
        try:
            self.directory.parent.rmdir()  # only if nothing else is there
        except OSError:
            pass

    def save(self, unit: str, data: dict, *, seconds: float, end: str | None) -> None:
        """Keep a finished unit, then print its progress line."""
        if unit not in self.units:
            raise ValueError(f"{unit!r} is not a unit of this run")
        write_atomic(self._path(unit), {"unit": unit, "data": data})
        self.seconds.append(seconds)
        left = len(self.units) - len(self.finished())
        self.out(progress_line(unit, seconds, end, left, self.seconds))


def progress_line(
    unit: str, seconds: float, end: str | None, left: int, times: list[float]
) -> str:
    """One unit's line: its time and end state, the units left, the time left
    (the mean time of the units this process ran, times the units left)."""
    eta = ""
    if left and times:
        rest = sum(times) / len(times) * left
        eta = f", about {_duration(rest)} left"
    return f"{unit}: {seconds:.0f} s, {end or '-'}; {left} units left{eta}"


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"
