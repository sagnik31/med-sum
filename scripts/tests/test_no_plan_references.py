"""
No committed file may point at the numbering of the project's planning
notes: "decision 12", "section 8.3", "question 5", "bug 1", "step 4 of the
plan", "Phase B" and the like. The notes aren't in the repository, so such
a pointer explains nothing. A comment states its rule instead, and cites
the ADR that records it ("See docs/adr/00NN-<name>.md"). See
docs/adr/README.md.

The check reads every file git tracks, as text. It is deliberately narrow,
to keep false alarms rare: a bare "step 4" stays allowed (the README
numbers its own setup steps), and so does a step number inside a test's
data. This file is the one exemption: it has to quote what it looks for.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAX_BYTES = 2_000_000  # larger tracked files are generated data, not prose

PATTERNS = [
    # "decision 12", "decisions 10-13", "section 8.3", "question 5", "bug 1"
    re.compile(r"\b(decisions?|sections?|questions?|bugs?)\s+\d", re.IGNORECASE),
    # "step 6 of the plan", "section 8.4 of the plan", "bug 1 of the plan"
    re.compile(r"\bof\s+the\s+plan\b", re.IGNORECASE),
    # the plan's phases and its lettered steps: "Phase B", "step 11c"
    re.compile(r"\bPhase\s+[AB]\b"),
    re.compile(r"\bsteps?\s+\d+[a-e]\b", re.IGNORECASE),
    # identifiers: test_..._decision_13, ..._section_8_8, test_bug2_...
    re.compile(r"(decision|section|question)_\d|(?<![a-z])bug\d", re.IGNORECASE),
    # the planning file itself
    re.compile(r"\bHANDOVER\b"),
]


def plan_references(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line that cites the plan's numbering."""
    return [
        (n, line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if any(p.search(line) for p in PATTERNS)
    ]


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available, or this is not a checkout")
    return [REPO / name for name in out.decode("utf-8").split("\0") if name]


def read_text(path: Path) -> str | None:
    """The file as text, or None for a binary, very large or missing file."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def test_no_committed_file_cites_the_plans_numbering():
    found = []
    for path in tracked_files():
        if path.resolve() == Path(__file__).resolve():
            continue
        text = read_text(path)
        if text is None:
            continue
        for n, line in plan_references(text):
            found.append(f"{path.relative_to(REPO).as_posix()}:{n}: {line[:120]}")
    assert not found, (
        "These lines cite the planning notes' numbering. State the rule and "
        "cite its ADR instead (docs/adr/README.md):\n" + "\n".join(found)
    )


D, S, Q, B = "decision", "section", "question", "bug"


@pytest.mark.parametrize(
    "line",
    [
        f"-- The database holds synthetic data only ({D} 16).",
        f"Derived values ({D}s 10-13, 26)",
        f"# The validators of {S} 8.3",
        f"Validator 9 (open {Q} 5, on its default)",
        f"# Remark attribution ({B} 1 of the plan)",
        f"# {B.title()} 2: a diagnosis invented from a single value",
        "Reset the database (step 6 of the plan).",
        "Until Phase B a patient is a login.",
        "Done in step 11c.",
        f"def test_the_document_date_follows_{D}_13():",
        f"def test_{B}5_documents_are_ordered():",
        "See " + "HANDOVER" + ".md for the plan.",
    ],
)
def test_a_plan_reference_is_caught(line):
    assert plan_references(line)


@pytest.mark.parametrize(
    "line",
    [
        "reset the database (with the virtual environment of step 4 active)",
        "db/schema.sql  the whole schema (no migrations; see step 2 of setup)",
        '"run 3f2a9c1e step 7",',
        "See docs/adr/0003-printed-and-derived-values.md.",
        "a decision that changes gets a new record",
        "LC-16: value is one of its own range's bounds",
        "the section below lists every check",
        "phase of the moon, Phase C",
        'select = ["E4", "E7", "E9", "F"]',
        "log.debug2 = True",
    ],
)
def test_ordinary_text_is_not_caught(line):
    assert not plan_references(line)
