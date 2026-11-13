"""
Tool-calling probe: can each local model call a read-only tool through
Ollama, and does it call it accurately?

    python -m evals.toolprobe --live [--model qwen3.5:4b] [--out DIR]

Each case is one short text-only exchange over an invented two-page lab
report (no page image, no dataset document): the model is offered the
tools of tools.TOOLS and a request shaped like a repair finding. A case
scores whether the model called a tool at all, called the expected one,
with arguments that fit its schema (and with the schema's own types), and
whether the arguments are on target (the page and lines the request points
at). The two-turn case also sends the tool's answer back and checks the
model's final reply. The no-tool case checks the model doesn't call a tool
when the request already holds the answer.

This measures the mechanism only, before any repair loop is built; the
repair comparison itself is part of the eval run. Every page line here is
invented, so the report holds no patient data. Output: a table on stdout
and probe.json in scripts/evals/results/<date>-tool-probe/ (not committed).
"""

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import llm
from record.document import PageTranscription
from tools import TOOLS, ToolSource, invoke, specs

RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"

# Room for a few tool calls or a one-line answer; a probe reply is short.
MAX_TOKENS = 256
# Most model turns one case may take (the two-turn case needs two).
MAX_TURNS = 3

SYSTEM = (
    "You check a parsed medical document against its transcription. The "
    "transcription is split into pages and numbered lines. Use the tools to "
    "read lines; never guess what a line says. When you have what you need, "
    "answer in one short line."
)

# An invented lab report; no line comes from the dataset.
PAGES = (
    PageTranscription(
        page=1,
        lines=[
            "# Hillcrest Pathology Services",
            "Patient: Mr. Arun Probe    Age/Sex: 47 Y / M",
            "Collected: 02/02/2025    Reported: 03/02/2025",
            "",
            "## RENAL PROFILE",
            "| Test | Result | Unit | Reference Range |",
            "|---|---|---|---|",
            "| Serum Urea | 31 | mg/dL | 15 - 40 |",
            "| Serum Creatinine | 1.1 | mg/dL | 0.7 - 1.3 |",
            "| Serum Uric Acid | 6.9 | mg/dL | 3.5 - 7.2 |",
            "",
            "## COMPLETE BLOOD COUNT",
            "| Test | Result | Unit | Reference Range |",
            "|---|---|---|---|",
            "| Haemoglobin | 13.8 | g/dL | 13.0 - 17.0 |",
            "| Platelet Count | 2.4 | lakh/cumm | 1.5 - 4.1 |",
        ],
    ),
    PageTranscription(
        page=2,
        lines=[
            "## GLUCOSE",
            "| Test | Result | Unit | Reference Range |",
            "|---|---|---|---|",
            "| Fasting Glucose | 104 | mg/dL | 70 - 100 | H |",
            "| Post Prandial Glucose | 131 | mg/dL | 70 - 140 |",
            "",
            "Remark: Fasting glucose slightly raised; repeat advised.",
            "*** End of Report ***",
        ],
    ),
)
SOURCE = ToolSource(pages=PAGES)


@dataclass(frozen=True)
class Target:
    """The call a case expects: a tool, a page, and lines it must cover."""

    tool: str
    page: int
    covers: tuple[int, int]  # the lines the call must include
    exact: tuple[int, int] | None = None  # when the request names exact lines
    max_span: int = 12  # a call reading far more than it needs is off target

    def hit(self, args: dict) -> bool:
        try:
            page, first, last = int(args["page"]), int(args["first"]), int(args["last"])
        except (KeyError, TypeError, ValueError):
            return False
        if page != self.page:
            return False
        if self.exact is not None:
            return (first, last) == self.exact
        lo, hi = self.covers
        return first <= lo and last >= hi and last - first + 1 <= self.max_span


@dataclass(frozen=True)
class Case:
    name: str
    request: str
    target: Target | None  # None: the model should answer without a tool
    answer: str | None = None  # a regex the final reply must match


CASES = (
    Case(
        "explicit_lines",
        "Show me lines 8 to 10 of page 1.",
        Target("get_lines", 1, (8, 10), exact=(8, 10)),
    ),
    Case(
        "finding_cites_a_line",
        "A validator reported: results[1] (Serum Creatinine): value_text "
        "'1.1' is not in its quote, which cites page 1, line 9. Read the "
        "cited line and the lines around it.",
        Target("get_lines", 1, (9, 9)),
    ),
    Case(
        "finding_on_page_two",
        "A validator reported: results[0] (Fasting Glucose): the printed "
        "flag cites page 2, line 4, and the quote does not match. Read that "
        "part of the page.",
        Target("get_lines", 2, (4, 4)),
    ),
    Case(
        "find_the_right_line",
        "The parse cites page 1, line 12 for the result 'Haemoglobin 13.8 "
        "g/dL', but line 12 is a heading. The haemoglobin row is somewhere "
        "in lines 11-16 of page 1. Find it and reply with only its line "
        "number.",
        Target("get_lines", 1, (11, 16)),
        answer=r"\b15\b",
    ),
    Case(
        "no_tool_needed",
        "Page 2, line 8 reads: '*** End of Report ***'. Does that line hold "
        "a test result? Reply yes or no.",
        None,
        answer=r"(?i)\bno\b",
    ),
)


@dataclass
class TurnRecord:
    seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    calls: list[dict] = field(default_factory=list)
    content_chars: int = 0


@dataclass
class CaseResult:
    case: str
    model: str
    called: bool = False
    right_tool: bool = False
    well_formed: bool = False
    exact_types: bool = False
    on_target: bool = False
    answered: bool | None = None  # only for cases with an answer pattern
    passed: bool = False
    error: str | None = None
    turns: list[TurnRecord] = field(default_factory=list)
    final_reply: str = ""


def run_case(case: Case, role: llm.ModelRole) -> CaseResult:
    """One exchange of up to MAX_TURNS model turns on the model of `role`."""
    result = CaseResult(case=case.name, model=llm.model_for(role))
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": case.request},
    ]
    first_call = None
    for _ in range(MAX_TURNS):
        start = time.monotonic()
        try:
            turn = llm.chat_with_tools(
                messages, tools=specs(TOOLS), max_tokens=MAX_TOKENS, model_role=role
            )
        except llm.ModelOutputError as e:
            result.error = type(e).__name__
            break
        record = TurnRecord(
            seconds=round(time.monotonic() - start, 1),
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
            content_chars=len(turn.content),
        )
        result.turns.append(record)
        if not turn.tool_calls:
            result.final_reply = turn.content
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
            outcome = invoke(TOOLS, c.name, c.arguments, SOURCE)
            record.calls.append(
                {"name": c.name, "arguments": c.arguments, **outcome.log_fields(),
                 "exact_types": outcome.exact_types}
            )  # fmt: skip
            if first_call is None:
                first_call = (c, outcome)
            messages.append(
                {"role": "tool", "tool_name": c.name, "content": outcome.for_model()}
            )
        if case.answer is None and case.target is not None:
            break  # one call is all a single-turn case needs
    score(case, result, first_call)
    return result


def score(case: Case, result: CaseResult, first_call) -> None:
    if first_call is not None:
        call, outcome = first_call
        result.called = True
        result.right_tool = case.target is not None and call.name == case.target.tool
        result.well_formed = outcome.ok or outcome.error.kind == "not_found"
        result.exact_types = bool(outcome.exact_types)
        args = call.arguments if isinstance(call.arguments, dict) else {}
        result.on_target = (
            case.target is not None and result.right_tool and case.target.hit(args)
        )
    if case.answer is not None:
        result.answered = bool(re.search(case.answer, result.final_reply))
    if case.target is None:
        result.passed = not result.called and bool(result.answered)
    else:
        result.passed = (
            result.on_target and result.well_formed and result.answered is not False
        )


def run(models: list[str]) -> list[CaseResult]:
    roles = {llm.model_for("primary"): "primary", llm.model_for("fallback"): "fallback"}
    results = []
    for model in models:
        if model not in roles:
            raise SystemExit(f"{model} is not a configured model ({', '.join(roles)})")
        for case in CASES:
            r = run_case(case, roles[model])
            results.append(r)
            print(_row(r), flush=True)
    return results


def _mark(v) -> str:
    return "-" if v is None else "yes" if v else "no"


def _row(r: CaseResult) -> str:
    secs = sum(t.seconds for t in r.turns)
    tokens = sum((t.prompt_tokens or 0) + (t.completion_tokens or 0) for t in r.turns)
    cols = [r.model, r.case, _mark(r.called), _mark(r.right_tool),
            _mark(r.well_formed), _mark(r.exact_types), _mark(r.on_target),
            _mark(r.answered), "PASS" if r.passed else "fail",
            f"{secs:.0f}s", str(tokens), r.error or ""]  # fmt: skip
    return " | ".join(cols)


HEADER = (
    "model | case | called | right tool | well formed | exact types | "
    "on target | answered | result | time | tokens | error"
)


def summary(results: list[CaseResult]) -> dict:
    out = {}
    for model in dict.fromkeys(r.model for r in results):
        rs = [r for r in results if r.model == model]
        tool_cases = [r for r, c in zip(rs, CASES) if c.target is not None]
        out[model] = {
            "passed": sum(r.passed for r in rs),
            "cases": len(rs),
            "called_when_needed": sum(r.called for r in tool_cases),
            "on_target": sum(r.on_target for r in tool_cases),
            "tool_cases": len(tool_cases),
            "seconds": round(sum(t.seconds for r in rs for t in r.turns), 1),
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--model", action="append", help="default: both models")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    models = args.model or [llm.model_for("primary"), llm.model_for("fallback")]
    print(HEADER, flush=True)
    results = run(models)
    out_dir = args.out or RESULTS_DIR / f"{date.today().isoformat()}-tool-probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "cases": [c.name for c in CASES],
        "models": {m: llm.model_digest(m) for m in models},
        "summary": summary(results),
        "results": [asdict(r) for r in results],
    }
    (out_dir / "probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {out_dir / 'probe.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
