"""
The MCP client eval: questions about the synthetic patients, answered by a
local model that can only read the records through the MCP server (so
through the Go API, its organisation scope and its audit log).

    python -m evals.mcp_eval --live [--model-role primary] [--split test] [--out DIR]
                             [--label NAME | --resume NAME]

A run saves its progress after each question and carries on with
--resume <name>; an outage of the model stops it (evals/progress.py).

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
- **located**: (value) a narrow citation (no more than NARROW_SLACK lines
  wider than the key's) points at the page and lines of the key's item, in
  the document of that file name;
- **correct**: (value) the key's value is in the answer as a whole word
  ("132" is not in "1132"), and in what a tool returned for the lines the
  answer cites, not merely somewhere in the exchange;
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
from evals import keys, progress
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


# How many lines wider than the key's item a citation may be and still
# locate it: a citation of a whole page or table says nothing about where
# the value is.
NARROW_SLACK = 2


def _has_cite(value: Any) -> bool:
    if isinstance(value, dict):
        return "cite" in value or any(_has_cite(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_cite(v) for v in value)
    return False


def _cited_entries(value: Any) -> list[tuple["Cite", str]]:
    """Each result entry with its own `cite`, and the text of that entry
    alone (entries nested in it, with cites of their own, left out)."""
    out: list[tuple[Cite, str]] = []
    if isinstance(value, dict):
        if isinstance(value.get("cite"), str):
            own = {k: v for k, v in value.items() if k != "cite" and not _has_cite(v)}
            text = json.dumps(own, ensure_ascii=False)
            out.extend((c, text) for c in citations_in(value["cite"]))
        for k, v in value.items():
            if k != "cite":
                out.extend(_cited_entries(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_cited_entries(v))
    return out


@dataclass
class Session:
    """What the tools returned in one exchange: citations, text, file names,
    and each cited entry's own text."""

    cites: list[Cite] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # document id -> file name
    entries: list[tuple[Cite, str]] = field(default_factory=list)

    def add(self, structured: dict | None, raw: str) -> None:
        self.text.append(raw)
        if not structured:
            return
        for c in structured.get("citations", []):
            self.cites.extend(citations_in(c))
        result = structured.get("result", {})
        for d in result.get("documents", []):
            self.names[d["document_id"].lower()] = d["original_name"]
        self.entries.extend(_cited_entries(result))


def _overlaps(a: Cite, b: Cite) -> bool:
    return (
        (a.kind, a.target, a.layer, a.page) == (b.kind, b.target, b.layer, b.page)
        and a.first is not None
        and b.first is not None
        and a.first <= b.last
        and b.first <= a.last
    )


def whole_word(value: str, text: str) -> bool:
    """Whether `value` is in `text` as a whole word or number: not inside a
    longer number or word ("132" is not in "1132" or "13.2" in "13.25")."""
    pattern = r"(?<![\w.])" + re.escape(_norm(value)) + r"(?![\w]|\.\d)"
    return re.search(pattern, _norm(text)) is not None


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
        first, last = question.lines
        locating = [
            q
            for q in quoted
            if q.kind == "document"
            and not q.layer
            and q.page == question.page
            and q.first is not None
            and q.first <= last
            and first <= q.last
            and (q.last - q.first) <= (last - first) + NARROW_SLACK
            and session.names.get(q.target) == question.file
        ]
        s.located = bool(locating)
        s.correct = whole_word(question.expected, bare) and any(
            _overlaps(q, cite) and whole_word(question.expected, text)
            for q in locating
            for cite, text in session.entries
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
            progress.stop_if_infrastructure(e)
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


async def run_all(
    questions: list[Question], role: llm.ModelRole, on_unit=None
) -> list[Outcome]:
    """Each question in turn; `on_unit(question_id, outcome)` keeps each."""
    from mcp import Client

    outcomes = []
    async with Client(server_parameters()) as client:
        specs = tool_specs(await client.list_tools())
        for q in questions:
            outcomes.append(await ask(client, q, specs, role))
            if on_unit is not None:
                on_unit(q.id, outcomes[-1])
    return outcomes


def stop_of(error: BaseException) -> "progress.RunStopped | None":
    """The RunStopped in an error, also inside the exception group that the
    MCP client's task group wraps it in."""
    if isinstance(error, progress.RunStopped):
        return error
    for inner in getattr(error, "exceptions", ()):
        found = stop_of(inner)
        if found is not None:
            return found
    return None


def outcome_of(data: dict) -> Outcome:
    """An outcome as its progress file kept it."""
    score = Score(**data["score"]) if data.get("score") is not None else None
    return Outcome(**{**data, "score": score})


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
    names = parser.add_mutually_exclusive_group()
    names.add_argument("--label", help="the run's name (default: date-mcp)")
    names.add_argument("--resume", metavar="NAME", help="carry on the named run")
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

    label = args.resume or args.label or f"{date.today().isoformat()}-mcp"
    model = llm.model_for(args.model_role)
    digests = progress.installed_digests(llm._client, [model])
    if digests[model] is None:
        print(f"no installed digest for {model}; is Ollama running?", file=sys.stderr)
        return 2
    try:
        run_progress = progress.Progress.open(
            label,
            progress.fingerprint(
                kind="mcp",
                models=[model],
                units=[q.id for q in questions],
                dataset=progress.json_hash([asdict(q) for q in questions]),
                digests=digests,
                selection={"split": args.split},
            ),  # fmt: skip
            resume=args.resume is not None,
        )
    except progress.ResumeRefused as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2

    def keep(question_id: str, outcome: Outcome) -> None:
        end = outcome.error or ("passed" if outcome.passed else "failed")
        run_progress.save(
            question_id, asdict(outcome), seconds=outcome.seconds, end=end
        )

    todo = [q for q in questions if run_progress.load(q.id) is None]
    try:
        anyio.run(run_all, todo, args.model_role, keep)
    except Exception as e:
        stop = stop_of(e)
        if stop is None:
            raise
        print(f"STOPPED: {stop}. Carry on with --resume {label}", file=sys.stderr)
        return 3
    outcomes = [outcome_of(run_progress.load(q.id)) for q in questions]
    print(report(outcomes))
    out = args.out or RESULTS_DIR / label
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
    run_progress.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
