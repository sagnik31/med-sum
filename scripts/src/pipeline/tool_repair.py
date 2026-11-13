"""
Repair with tools: the second way to repair a part, chosen by config.

The fixed repair (the default) sends the part's previous reply and what was
wrong with it, and asks for the whole reply again. The tool repair sends
the same, plus read-only tools (tools/: get_lines, search_transcription,
get_text_layer, get_item), and lets the model look before it answers:

    turn 1 .. budget-1   the model may call tools, or reply with its fix
    last turn            the fix, with the output schema enforced

The budget counts model turns, the one that gives the fix included
(MEDSUM_REPAIR_TOOL_TURNS, default 3: at most two turns of reading). A
malformed tool call, or a call to a tool that isn't offered, uses its turn
like any other, and its error goes back to the model. A reading turn may
make up to MAX_CALLS_PER_TURN calls; more are answered with an error.

Nothing about judging changes: the fix is checked against the parse
schema like any reply, and the validators judge the merged parse exactly
as after a fixed repair, so the end states are the same rules. Tools run
only inside a repair, and a repair only follows an error, so a document
that parses cleanly never reaches them; the budget bounds what a repair
can add. See docs/adr/0002-two-model-steps.md.

The last turn is a plain chat() call with the schema as its format (Ollama
doesn't document `format` together with `tools`), so the tool exchange is
written into that request as text: what was called and what came back.

Each tool call is a step of its own under the repair step (node "tool"),
which stores the tool's name, the arguments' numbers (text arguments only
as a hash and length) and the outcome with its sizes, never what the tool
returned: tool answers quote the pages. See
docs/adr/0008-patient-data-protection.md.
"""

import hashlib
import json
import os
import re
from typing import Literal

from pydantic import Field

import llm
from observability import current_step
from record.common import Strict
from tools import ToolResult, ToolSource, invoke, specs, subset

Variant = Literal["fixed", "tools"]
VARIANTS = ("fixed", "tools")
DEFAULT_TURNS = 3
MAX_CALLS_PER_TURN = 3
# The tools a repair may use. retranscribe_page is not among them: a page
# read again is a vision call of minutes on a CPU, more than a repair may add.
REPAIR_TOOLS = ("get_lines", "search_transcription", "get_text_layer", "get_item")

READING_NOTE = (
    "\n\nYou may use the tools to read the transcription, the PDF's own text "
    "and the record's items before you answer. You have {turns} turns in all, "
    "this answer included; the last one must be the whole JSON. When you are "
    "ready, reply with the whole JSON instead of calling a tool."
)
FINAL_NOTE = (
    "Your turns for reading are used. Reply now with the whole JSON for "
    "this part, with the problems fixed."
)


def variant(environ=os.environ) -> Variant:
    """The repair in use: MEDSUM_REPAIR, fixed unless set to tools."""
    value = environ.get("MEDSUM_REPAIR", "fixed").strip().lower() or "fixed"
    if value not in VARIANTS:
        raise ValueError(f"MEDSUM_REPAIR must be one of {', '.join(VARIANTS)}")
    return value  # type: ignore[return-value]


def turn_budget(environ=os.environ) -> int:
    """Model turns per tool repair, the fix included (at least 1)."""
    value = int(environ.get("MEDSUM_REPAIR_TOOL_TURNS", DEFAULT_TURNS))
    if value < 1:
        raise ValueError("MEDSUM_REPAIR_TOOL_TURNS must be at least 1")
    return value


class RepairRecord(Strict):
    """
    What one part's repair took, for the eval's rows: counts and sizes
    only, never content. Kept in the state (repair_log) for each repair.
    """

    attempt: int = Field(ge=1)
    part: int = Field(ge=0)
    model_role: Literal["primary", "fallback"]
    variant: Variant
    turns: int = Field(ge=0, description="Model turns, the fix's included.")
    tool_calls: int = 0
    malformed: int = 0  # arguments off the tool's schema
    unknown_tool: int = 0  # a tool not offered
    not_found: int = 0  # well formed, but named nothing there
    inexact_types: int = 0  # numbers sent as strings, read anyway
    over_limit: int = 0  # calls past MAX_CALLS_PER_TURN in one turn
    by_tool: dict[str, int] = {}
    fix_turn: Literal["reading", "final"] | None = Field(
        default=None, description="Where the reply that fit came from, if one did."
    )
    # The part's repair as a whole: every model call it made, both variants.
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


def strip_fence(text: str) -> str:
    """A reply wrapped in one Markdown code fence, unwrapped."""
    m = re.fullmatch(r"\s*```[a-zA-Z]*\s*\n(.*)\n\s*```\s*", text, re.DOTALL)
    return m.group(1) if m else text


def _argument_ref(arguments) -> dict:
    """What a tool step stores of the arguments: numbers, text as hash and length."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {"unparsed": _text_ref(arguments)}
    if not isinstance(arguments, dict):
        return {"type": type(arguments).__name__}
    out = {}
    for name, value in list(arguments.items())[:8]:
        key = str(name)[:40]
        if isinstance(value, bool) or value is None or isinstance(value, int | float):
            out[key] = value
        else:
            out[key] = _text_ref(str(value))
    return out


def _text_ref(text: str) -> dict:
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }


def run_tool(name: str, arguments, source: ToolSource) -> ToolResult:
    """One tool call, as a step of its own under the current (repair) step."""
    step = current_step()
    if step is None:
        return invoke(subset(*REPAIR_TOOLS), name, arguments, source)
    with step.run.step(
        "tool", input={"tool": str(name)[:64], "arguments": _argument_ref(arguments)}
    ) as tool_step:
        result = invoke(subset(*REPAIR_TOOLS), name, arguments, source)
        tool_step.set_output({**result.log_fields(), "exact_types": result.exact_types})
        if not result.ok:
            tool_step.fail(f"{result.error.kind}: {result.error.message}")
    return result


def _count(record: RepairRecord, result: ToolResult) -> None:
    record.tool_calls += 1
    record.by_tool[result.tool] = record.by_tool.get(result.tool, 0) + 1
    if result.error is not None:
        kind = result.error.kind
        if kind == "malformed_arguments":
            record.malformed += 1
        elif kind == "unknown_tool":
            record.unknown_tool += 1
        elif kind == "not_found":
            record.not_found += 1
    if result.exact_types is False:
        record.inexact_types += 1


def _offered(source: ToolSource) -> dict:
    """The repair's tools; get_text_layer only when a page has a text layer."""
    names = [n for n in REPAIR_TOOLS if n != "get_text_layer"]
    if any(p.text_layer for p in source.pages):
        names.append("get_text_layer")
    return subset(*names)


def _over_limit(name: str) -> str:
    return json.dumps(
        {
            "error": "over_limit",
            "message": f"at most {MAX_CALLS_PER_TURN} tool calls per turn; "
            f"{name} was not run",
        }
    )


def read_then_fix(
    messages: list[dict],
    *,
    source: ToolSource,
    model_role: str,
    max_tokens: int,
    budget: int,
    record: RepairRecord,
    try_fix,
    final_call,
):
    """
    The bounded exchange. `messages` end with the repair request.
    `try_fix(text)` returns the part's result when `text` is a reply that
    fits, else None; `final_call(messages)` makes the last turn (a chat()
    with the schema) and returns the part's result. Returns that result.
    """
    offered = _offered(source)
    tool_specs = specs(offered)
    exchange: list[str] = []  # the tool exchange, as text for the last turn
    convo = list(messages)
    convo[-1] = {**convo[-1], "content": convo[-1]["content"]
                 + READING_NOTE.format(turns=budget)}  # fmt: skip
    for _ in range(budget - 1):
        try:
            turn = llm.chat_with_tools(
                convo, tools=tool_specs, max_tokens=max_tokens, model_role=model_role
            )
        except llm.ModelUnavailableError:
            break  # not called: the last turn goes to chat(), which may reach another
        except llm.ModelOutputError:
            record.turns += 1  # a cut-off or failed turn is used all the same
            break
        record.turns += 1
        if not turn.tool_calls:
            fixed = try_fix(strip_fence(turn.content))
            if fixed is not None:
                record.fix_turn = "reading"
                return fixed
            break  # not a fix and no tool asked for: go to the last turn
        convo.append(
            {
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in turn.tool_calls
                ],
            }
        )
        for n, call in enumerate(turn.tool_calls):
            if n >= MAX_CALLS_PER_TURN:
                record.over_limit += 1
                answer = _over_limit(str(call.name)[:64])
            else:
                result = run_tool(call.name, call.arguments, source)
                _count(record, result)
                answer = result.for_model()
            convo.append(
                {"role": "tool", "tool_name": str(call.name), "content": answer}
            )
            exchange.append(
                f"You called {str(call.name)[:64]} with "
                f"{json.dumps(call.arguments, ensure_ascii=False, default=str)}; "
                f"it returned: {answer}"
            )
    final = list(messages)
    told = [final[-1]["content"]]
    if exchange:
        told.append("What the tools returned:\n" + "\n".join(exchange))
    told.append(FINAL_NOTE)
    final[-1] = {**final[-1], "content": "\n\n".join(told)}
    record.turns += 1
    result = final_call(final)
    if getattr(result, "parse", None) is not None:
        record.fix_turn = "final"
    return result
