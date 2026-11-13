"""
What a tool is, and how a model's call to one is checked and run.

A Tool has a name, a description written for the model, a Pydantic input
model (its JSON schema is what the model is shown) and an output model.
invoke() takes a call as the model sent it (a name and arguments, both
unchecked) and always returns a ToolResult: the typed output, or a
ToolError saying what was wrong. A malformed call never raises, so the
caller can count it, send the error back to the model and go on.

Results are data for the model; what may be logged about a call is
ToolResult.log_fields(): the tool, the outcome and sizes, never arguments
that could carry text or what the tool returned (the pages hold patient
content). See docs/adr/0008-patient-data-protection.md.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from record.document import DocumentRecord, PageTranscription


class ToolInput(BaseModel):
    """
    Base of every tool's input. Unknown arguments are errors. Numbers
    written as strings ("3") are accepted, as Pydantic's default does;
    whether a model sends them that way is measured, not punished.
    """

    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ToolSource:
    """
    What the tools read: one document's transcribed pages, in order (each
    with the PDF's own text when it has one), and the record built from its
    parse, when there is one.
    """

    pages: tuple[PageTranscription, ...]
    record: DocumentRecord | None = None

    def page(self, number: int) -> PageTranscription | None:
        return next((p for p in self.pages if p.page == number), None)

    @property
    def page_numbers(self) -> str:
        return ", ".join(str(p.page) for p in self.pages) or "none"


def cite(page: int, first: int, last: int | None = None) -> str:
    """Where lines are, as every tool answer gives it: p2:7, or p2:7-9."""
    return f"p{page}:{first}" if last in (None, first) else f"p{page}:{first}-{last}"


ErrorKind = Literal["unknown_tool", "malformed_arguments", "not_found"]


@dataclass(frozen=True)
class ToolError:
    """
    Why a call gave no answer. `message` is shown to the model and may be
    logged: it names fields and limits, never an argument's value.
    """

    kind: ErrorKind
    message: str


InT = TypeVar("InT", bound=ToolInput)
OutT = TypeVar("OutT", bound=ToolOutput)


class Tool(Generic[InT, OutT]):
    """
    A read-only tool. `run(source, input)` returns the output, or a
    ToolError when the input is well formed but names nothing there
    (a page the document doesn't have).
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[InT],
        output_model: type[OutT],
        run: Callable[[ToolSource, InT], OutT | ToolError],
    ):
        self.name = name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.run = run

    def spec(self) -> dict:
        """The tool as Ollama's `tools` list wants it (a function spec)."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


def specs(tools: Mapping[str, Tool]) -> list[dict]:
    return [t.spec() for t in tools.values()]


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one call: `output` or `error`, never both."""

    tool: str
    output: ToolOutput | None = None
    error: ToolError | None = None
    # Whether the arguments already had the schema's types (no "3" for 3).
    exact_types: bool | None = None
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    def for_model(self) -> str:
        """The content of the tool message sent back to the model."""
        if self.error is not None:
            return json.dumps({"error": self.error.kind, "message": self.error.message})
        return self.output.model_dump_json()

    def log_fields(self) -> dict[str, Any]:
        """What may be logged or traced about the call: no page content."""
        fields: dict[str, Any] = {"tool": self.tool, "ok": self.ok}
        if self.error is not None:
            fields["error_kind"] = self.error.kind
        fields.update(self.sizes)
        return fields


def invoke(tools: Mapping[str, Tool], name: str, arguments: Any, source: ToolSource):
    """Check a model's call against the tool's schema and run it."""
    tool = tools.get(name)
    if tool is None:
        known = ", ".join(sorted(tools))
        return ToolResult(
            tool=str(name)[:64],
            error=ToolError("unknown_tool", f"no tool by that name; tools: {known}"),
        )
    if isinstance(arguments, str):  # some models send the arguments as JSON text
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return _malformed(name, "arguments are not a JSON object")
    if not isinstance(arguments, Mapping):
        return _malformed(name, "arguments are not a JSON object")
    try:
        parsed = tool.input_model.model_validate(dict(arguments))
    except ValidationError as e:
        return _malformed(name, _describe(e))
    try:
        tool.input_model.model_validate(dict(arguments), strict=True)
        exact = True
    except ValidationError:
        exact = False
    answer = tool.run(source, parsed)
    if isinstance(answer, ToolError):
        return ToolResult(tool=name, error=answer, exact_types=exact)
    return ToolResult(tool=name, output=answer, exact_types=exact, sizes=_sizes(answer))


def _malformed(name: str, message: str) -> ToolResult:
    return ToolResult(tool=name, error=ToolError("malformed_arguments", message))


def _describe(error: ValidationError) -> str:
    """Field paths and Pydantic's messages, without the values sent."""
    parts = []
    for e in error.errors(include_input=False, include_url=False):
        where = ".".join(str(p) for p in e["loc"]) or "arguments"
        parts.append(f"{where}: {e['msg']}")
    return "; ".join(parts)


def _sizes(output: ToolOutput) -> dict[str, int]:
    """Counts a tool reports about its output (e.g. lines returned)."""
    report = getattr(output, "sizes", None)
    return report() if callable(report) else {}
