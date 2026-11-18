"""
The MCP client eval: questions about the synthetic patients, answered by a
local model that can only read the records through the MCP server (so
through the Go API, its organisation scope and its audit log).

    python -m evals.mcp_eval --live [--model-role primary] [--split test] [--out DIR]

Before a run: the synthetic dataset loaded into the development database
(python -m dataset.load --yes) and processed, the API running, and
scripts/mcp.env.local filled in (see the "MCP server" section of docs/api.md).
The server starts only under MEDSUM_ENV=dev.

Questions are built from the dataset's answer keys, never from a model:

- **value**: "What was <patient>'s <test> in <file>?", for lab results,
  and the strength or frequency of a prescribed medicine. Answerable: the
  value is in that document's record.
- **absent**: a common test that none of the patient's documents holds.
  The records hold no answer; the model should say so.

Each answer is scored on:

- **cited**: it quotes at least one citation of the tools' form;
- **grounded**: every citation it quotes covers lines a tool returned in
  this exchange (a model can't invent one);
- **numbers**: every number in it (citations aside) appears in the text the
  tools returned, the patient overview's check of numbers reused;
- **located**: (value) a citation points at the page and lines of the key's
  item, in the document of that file name;
- **correct**: (value) the key's value is in the answer;
- **abstained**: it says the records hold no answer and cites nothing.

A value question passes when it is cited, grounded, numbers-checked,
located and correct; an absent one when it abstained and gave no number.
Output: a table with Wilson intervals on stdout, and results.json in
scripts/evals/results/<date>-mcp/ (not committed; it quotes synthetic
values). Not run yet: it needs a live model and a loaded database.
See docs/adr/0012-shared-tool-layer.md.
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import llm
from evals import keys
from evals.stats import wilson
from summary.overview import numbers

RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"
SRC_DIR = Path(__file__).resolve().parents[1]

# Most model turns one question may take: find the patient, list the
# documents, read one or two, answer.
MAX_TURNS = 8
MAX_TOKENS = 512

SYSTEM = (
    "You answer hospital staff's questions about a patient's medical "
    "documents, using only the tools. Never guess, infer or advise. Quote the "
    "citation of every value you give, exactly as the tool returned it, e.g. "
    "(document:<id>#p2:7). If the records hold no answer, reply exactly: "
    "The records hold no answer."
)

# Common tests to ask about when a patient's documents don't hold them.
ABSENT_TESTS = (
    "HbA1c",
    "Serum Ferritin",
    "Vitamin D",
    "TSH",
    "Serum Creatinine",
    "Total Cholesterol",
    "PSA",
)

CITATION = re.compile(
    r"(patient|document):([0-9a-fA-F-]{36})(?:#(text-layer:)?p(\d+):(\d+)(?:-(\d+))?)?"
)
ABSTAIN = re.compile(
    r"records hold no answer|no (?:such )?(?:result|record|answer) in the records", re.I
)


# -------------------------------------------------------------------
# Questions
# -------------------------------------------------------------------
@dataclass(frozen=True)
class Question:
    id: str
    kind: str  # value or absent
    text: str
    patient: str  # the patient's name, as registered
    file: str | None = None  # the document's file name (value questions)
    expected: str | None = None  # the key's value (value questions)
    page: int | None = None
    lines: tuple[int, int] | None = None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def build_questions(
    split: str | None = "test", dataset_dir=keys.DATASET_DIR
) -> list[Question]:
    """Value and absent questions from the dataset's keys, in a fixed order."""
    questions: list[Question] = []
    held: dict[str, set[str]] = {}
    patients = {p["key"]: p for p in keys.manifest(dataset_dir)["patients"]}
    for doc_id, entry in keys.documents(dataset_dir).items():
        key = keys.answer_key(doc_id, dataset_dir)
        patient = patients[entry["patient"]]
        names = held.setdefault(patient["key"], set())
        names.update(_norm(o.printed.test_name) for o in key.observations)
        if split is not None and entry.get("split") != split:
            continue
        file = Path(entry["file"]).name
        obs = key.observations
        picks = [obs[0], obs[-1]] if len(obs) > 1 else obs  # the first and the last
        for o in picks:
            c = o.printed.citation
            questions.append(
                Question(
                    id=f"{doc_id}-{o.id}",
                    kind="value",
                    text=f"What was {patient['name']}'s {o.printed.test_name} result in {file}?",
                    patient=patient["name"],
                    file=file,
                    expected=o.printed.value_text,
                    page=c.page,
                    lines=(c.first_line, c.last_line),
                )
            )
        for m in key.medications[:1]:
            asked = (
                "strength"
                if m.printed.strength
                else "frequency"
                if m.printed.frequency
                else None
            )
            if asked is None:
                continue
            c = m.printed.citation
            questions.append(
                Question(
                    id=f"{doc_id}-{m.id}",
                    kind="value",
                    text=f"What {asked} of {m.printed.name} was prescribed to {patient['name']} in {file}?",
                    patient=patient["name"],
                    file=file,
                    expected=getattr(m.printed, asked),
                    page=c.page,
                    lines=(c.first_line, c.last_line),
                )
            )
    for key_name, patient in patients.items():
        absent = next(
            (t for t in ABSENT_TESTS if _norm(t) not in held.get(key_name, set())), None
        )
        if absent is None:
            continue
        questions.append(
            Question(
                id=f"{key_name}-absent",
                kind="absent",
                text=f"What was {patient['name']}'s {absent} result?",
                patient=patient["name"],
            )
        )
    return questions


# -------------------------------------------------------------------
# Scoring
# -------------------------------------------------------------------
@dataclass(frozen=True)
class Cite:
    kind: str
    target: str
    layer: bool = False
    page: int | None = None
    first: int | None = None
    last: int | None = None

    @classmethod
    def parse(cls, m: re.Match) -> "Cite":
        page = int(m.group(4)) if m.group(4) else None
        first = int(m.group(5)) if m.group(5) else None
        last = int(m.group(6)) if m.group(6) else first
        return cls(m.group(1), m.group(2).lower(), bool(m.group(3)), page, first, last)

    def within(self, other: "Cite") -> bool:
        """Whether this citation covers no more than `other` does."""
        if (self.kind, self.target, self.layer) != (
            other.kind,
            other.target,
            other.layer,
        ):
            return False
        if self.page is None:
            return True
        return (
            other.page == self.page
            and other.first is not None
            and other.first <= self.first
            and self.last <= other.last
        )


def citations_in(text: str) -> list[Cite]:
    return [Cite.parse(m) for m in CITATION.finditer(text)]


@dataclass
class Session:
    """What the tools returned in one exchange: citations, text, file names."""

    cites: list[Cite] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # document id -> file name

    def add(self, structured: dict | None, raw: str) -> None:
        self.text.append(raw)
        if not structured:
            return
        for c in structured.get("citations", []):
            self.cites.extend(citations_in(c))
        for d in structured.get("result", {}).get("documents", []):
            self.names[d["document_id"].lower()] = d["original_name"]


@dataclass
class Score:
    cited: bool = False
    grounded: bool = False
    numbers: bool = False
    located: bool | None = None
    correct: bool | None = None
    abstained: bool = False
    unknown_numbers: list[str] = field(default_factory=list)

    def passed(self, kind: str) -> bool:
        if kind == "absent":
            return self.abstained and self.numbers
        return all(
            (self.cited, self.grounded, self.numbers, self.located, self.correct)
        )


def score(question: Question, answer: str, session: Session) -> Score:
    quoted = citations_in(answer)
    bare = CITATION.sub(" ", answer)
    seen = set().union(*(numbers(t) for t in session.text)) if session.text else set()
    unknown = sorted(numbers(bare) - seen)
    s = Score(
        cited=bool(quoted),
        grounded=bool(quoted)
        and all(any(q.within(c) for c in session.cites) for q in quoted),
        numbers=not unknown,
        abstained=bool(ABSTAIN.search(answer)) and not quoted,
        unknown_numbers=unknown,
    )
    if question.kind == "value":
        s.correct = _norm(question.expected) in _norm(bare)
        first, last = question.lines
        s.located = any(
            q.kind == "document"
            and not q.layer
            and q.page == question.page
            and q.first is not None
            and q.first <= last
            and first <= q.last
            and session.names.get(q.target) == question.file
            for q in quoted
        )
    return s


# -------------------------------------------------------------------
# One exchange
# -------------------------------------------------------------------
@dataclass
class Outcome:
    question: str
    kind: str
    model: str
    answer: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    turns: int = 0
    seconds: float = 0.0
    error: str | None = None
    score: Score | None = None
    passed: bool = False


def tool_specs(listed) -> list[dict]:
    """The server's tools as Ollama's function specs."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in listed.tools
    ]


async def ask(
    client, question: Question, specs: list[dict], role: llm.ModelRole, chat=None
) -> Outcome:
    """One question, up to MAX_TURNS model turns, tools over `client`."""
    chat = chat or llm.chat_with_tools
    outcome = Outcome(
        question=question.id, kind=question.kind, model=llm.model_for(role)
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question.text},
    ]
    session = Session()
    started = time.monotonic()
    for _ in range(MAX_TURNS):
        try:
            turn = chat(messages, tools=specs, max_tokens=MAX_TOKENS, model_role=role)
        except llm.ModelOutputError as e:
            outcome.error = type(e).__name__
            break
        outcome.turns += 1
        if not turn.tool_calls:
            outcome.answer = turn.content
            break
        messages.append(
            {
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in turn.tool_calls
                ],
            }
        )
        for c in turn.tool_calls:
            args = (
                c.arguments
                if isinstance(c.arguments, dict)
                else _json_object(c.arguments)
            )
            result = await client.call_tool(c.name, args)
            raw = result.content[0].text if result.content else ""
            session.add(None if result.is_error else result.structured_content, raw)
            outcome.tool_calls.append({"name": c.name, "ok": not result.is_error})
            messages.append({"role": "tool", "tool_name": c.name, "content": raw})
    else:
        outcome.error = "too_many_turns"
    outcome.seconds = round(time.monotonic() - started, 1)
    outcome.score = score(question, outcome.answer, session)
    outcome.passed = outcome.error is None and outcome.score.passed(question.kind)
    return outcome


def _json_object(text: Any) -> dict:
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except json.JSONDecodeError:
        value = None
    return value if isinstance(value, dict) else {}


# -------------------------------------------------------------------
# Report and command line
# -------------------------------------------------------------------
def report(outcomes: list[Outcome]) -> str:
    lines = ["| Questions | Measure | Result |", "| --- | --- | --- |"]
    for kind in ("value", "absent"):
        group = [o for o in outcomes if o.kind == kind]
        if not group:
            continue
        measures = {"passed": [o.passed for o in group]}
        scored = [o.score for o in group if o.score is not None]
        if kind == "value":
            for name in ("cited", "grounded", "numbers", "located", "correct"):
                measures[name] = [bool(getattr(s, name)) for s in scored]
        else:
            measures["abstained"] = [s.abstained for s in scored]
        for name, hits in measures.items():
            lines.append(f"| {kind} | {name} | {wilson(sum(hits), len(hits)).text()} |")
    errors = sum(o.error is not None for o in outcomes)
    lines.append("")
    lines.append(f"{len(outcomes)} questions; {errors} ended without an answer.")
    return "\n".join(lines)


def server_parameters(environ=os.environ):
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "medsum_mcp"],
        cwd=str(SRC_DIR),
        env=dict(environ),
    )


async def run_all(questions: list[Question], role: llm.ModelRole) -> list[Outcome]:
    from mcp import Client

    async with Client(server_parameters()) as client:
        specs = tool_specs(await client.list_tools())
        return [await ask(client, q, specs, role) for q in questions]


def main(argv=None, environ=os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--live", action="store_true", help="run it (a live model and the API)"
    )
    parser.add_argument(
        "--model-role", choices=["primary", "fallback"], default="primary"
    )
    parser.add_argument(
        "--split", default="test", help="dataset split; 'all' for every document"
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    questions = build_questions(None if args.split == "all" else args.split)
    if not args.live:
        print(
            f"{len(questions)} questions; pass --live to run them (a live model and the API)."
        )
        return 2
    if environ.get("MEDSUM_ENV") != "dev":
        print(
            "the MCP eval runs only with MEDSUM_ENV=dev (synthetic data only)",
            file=sys.stderr,
        )
        return 2
    import anyio

    outcomes = anyio.run(run_all, questions, args.model_role)
    print(report(outcomes))
    out = args.out or RESULTS_DIR / f"{date.today().isoformat()}-mcp"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps(
            {
                "model": llm.model_for(args.model_role),
                "outcomes": [asdict(o) for o in outcomes],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
