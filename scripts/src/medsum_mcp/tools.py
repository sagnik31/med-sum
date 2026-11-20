"""
The MCP server's tools: two that find a patient's documents, and the five
read-only document tools of tools/ (the ones the repair step uses
in-process), each with a `document_id` added to its input.

Each call is one GET to the Go API, labelled with the tool, so it is one
audit row naming the tool. A call whose arguments don't fit the schema is
answered before any request: it reads nothing, so it leaves no audit row,
and only its error kind is logged.

Every answer is {"citations": [...], "result": {...}}: where each value of
the result comes from, and the result, in which every listed entry carries
its own `cite`. Citations are

    patient:<id>                       the patient's registration
    document:<id>                      the document's upload (name, date)
    document:<id>#p2:7-9               lines of the document's transcription
    document:<id>#text-layer:p3:1-40   lines of the PDF's own text layer

The document tools read the record as clinicians reviewed it: every
correction applied, as the patient summary shows it.
See docs/adr/0012-shared-tool-layer.md.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError, create_model

import tools as shared
from medsum_mcp.api import ApiClient, ApiError
from record.document import DocumentRecord
from record.review import reviewed_view
from tools.base import ToolInput, ToolOutput, ToolSource, _describe

log = logging.getLogger("medsum_mcp")

# What a tool's run can raise besides ApiError: the API unreachable or its
# answer unreadable (not JSON, or a record that doesn't fit the schema).
# Each is answered as "unavailable", and logged by type only.
UNAVAILABLE = (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError)

UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# Most patients find_patients returns; a broader search should be narrowed.
MAX_PATIENTS = 25

INSTRUCTIONS = (
    "Med-Sum holds hospital staff's medical documents, transcribed and parsed. "
    "These tools read them; they never infer or advise. Find the patient with "
    "find_patients, their documents with list_documents, then read a "
    "document with list_items, get_item, search_transcription or get_lines. "
    "Every result lists its citations: quote the citation of every value you "
    "use, and say the records hold no answer when no tool result supports one."
)


@dataclass(frozen=True)
class ErrorAnswer:
    kind: str  # malformed_arguments, not_found, not_allowed, unavailable, unknown_tool
    message: str

    def as_dict(self) -> dict:
        return {"error": self.kind, "message": self.message}


@dataclass(frozen=True)
class Answer:
    """One call's outcome: `structured` (citations and result) or `error`."""

    tool: str
    structured: dict | None = None
    error: ErrorAnswer | None = None
    api_status: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class ServerTool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    run: Callable[[ApiClient, Any], Awaitable[dict | ErrorAnswer]]

    def input_schema(self) -> dict:
        return _schema(self.input_model)

    def output_schema(self) -> dict:
        return _schema(self.output_model)


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema


def _answer_model(name: str, result: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        name,
        citations=(
            list[str],
            Field(description="Where every value of the result comes from."),
        ),
        result=(result, ...),
    )


def _cited(result: dict, citations: list[str]) -> dict:
    return {"citations": citations, "result": result}


def _api_error(e: ApiError) -> ErrorAnswer:
    if e.status == 404:
        return ErrorAnswer("not_found", str(e))
    if e.status == 403:
        return ErrorAnswer("not_allowed", str(e))
    return ErrorAnswer("unavailable", str(e))


# -------------------------------------------------------------------
# find_patients, list_documents
# -------------------------------------------------------------------
class FindPatientsInput(ToolInput):
    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="Part of the patient's name, or the start of the MRN; "
        "leave out to list the organisation's patients.",
    )


class PatientEntry(ToolOutput):
    patient_id: str
    full_name: str
    mrn: str
    date_of_birth: str | None
    age_years: int | None
    sex: str | None
    cite: str


class FindPatientsOutput(ToolOutput):
    patients: list[PatientEntry]
    more: bool = Field(description=f"True when more than {MAX_PATIENTS} matched.")


async def _find_patients(api: ApiClient, args: FindPatientsInput) -> dict:
    params = {"q": args.query} if args.query else None
    rows = await api.get("/patients", tool="find_patients", params=params)
    patients = [
        PatientEntry(
            patient_id=p["id"],
            full_name=p["full_name"],
            mrn=p["mrn"],
            date_of_birth=p.get("date_of_birth"),
            age_years=p.get("age_years"),
            sex=p.get("sex"),
            cite=f"patient:{p['id']}",
        )
        for p in rows[:MAX_PATIENTS]
    ]
    out = FindPatientsOutput(patients=patients, more=len(rows) > MAX_PATIENTS)
    return _cited(out.model_dump(mode="json"), [p.cite for p in patients])


FIND_PATIENTS = ServerTool(
    name="find_patients",
    description=(
        "Find patients of your organisation by part of the name or the start "
        f"of the MRN (at most {MAX_PATIENTS}). Returns each patient's id, name, "
        "MRN, date of birth (or age) and sex, as registered."
    ),
    input_model=FindPatientsInput,
    output_model=_answer_model("FindPatientsAnswer", FindPatientsOutput),
    run=_find_patients,
)


class ListDocumentsInput(ToolInput):
    patient_id: str = Field(
        pattern=UUID_PATTERN, description="A patient's id, from find_patients."
    )


class DocumentEntry(ToolOutput):
    document_id: str
    original_name: str
    uploaded_at: str
    insight_status: str | None = Field(
        description="verified (every value passed its checks or a clinician "
        "reviewed it), needs_review (some values await a clinician), "
        "processing, failed or not_started."
    )
    duplicate_of: str | None = Field(
        description="The earlier upload this one is an exact copy of, if any."
    )
    cite: str


class ListDocumentsOutput(ToolOutput):
    patient_id: str
    documents: list[DocumentEntry]


async def _list_documents(api: ApiClient, args: ListDocumentsInput) -> dict:
    rows = await api.get(
        f"/patients/{args.patient_id}/documents", tool="list_documents"
    )
    docs = [
        DocumentEntry(
            document_id=d["id"],
            original_name=d["original_name"],
            uploaded_at=d["uploaded_at"],
            insight_status=d.get("insight_status") or None,
            duplicate_of=d.get("duplicate_of"),
            cite=f"document:{d['id']}",
        )
        for d in rows
    ]
    out = ListDocumentsOutput(patient_id=args.patient_id, documents=docs)
    return _cited(out.model_dump(mode="json"), [d.cite for d in docs])


LIST_DOCUMENTS = ServerTool(
    name="list_documents",
    description=(
        "List a patient's documents, newest first: each one's id, file name, "
        "upload time and processing status. Values in a needs_review document "
        "may still be wrong."
    ),
    input_model=ListDocumentsInput,
    output_model=_answer_model("ListDocumentsAnswer", ListDocumentsOutput),
    run=_list_documents,
)


# -------------------------------------------------------------------
# The document tools of tools/
# -------------------------------------------------------------------
def _document_cites(document_id: str, value: Any) -> Any:
    """Every `cite` in a result, made to name its document."""
    if isinstance(value, dict):
        return {
            k: f"document:{document_id}#{v}" if k == "cite" and isinstance(v, str)
            else _document_cites(document_id, v)
            for k, v in value.items()
        }  # fmt: skip
    if isinstance(value, list):
        return [_document_cites(document_id, v) for v in value]
    return value


def _collect_cites(value: Any) -> list[str]:
    if isinstance(value, dict):
        own = [value["cite"]] if isinstance(value.get("cite"), str) else []
        return own + [
            c for k, v in value.items() if k != "cite" for c in _collect_cites(v)
        ]
    if isinstance(value, list):
        return [c for v in value for c in _collect_cites(v)]
    return []


def _citations(tool: str, document_id: str, result: dict) -> list[str]:
    if tool == "get_text_layer":
        if not result["lines"]:
            return []
        first, last = result["lines"][0]["number"], result["lines"][-1]["number"]
        where = shared.cite(result["page"], first, last)
        return [f"document:{document_id}#text-layer:{where}"]
    return list(dict.fromkeys(_collect_cites(result)))


def _document_tool(tool: shared.Tool) -> ServerTool:
    input_model = create_model(
        "".join(p.title() for p in tool.name.split("_")) + "McpInput",
        __base__=tool.input_model,
        document_id=(
            str,
            Field(
                pattern=UUID_PATTERN,
                description="A document's id, from list_documents.",
            ),
        ),
    )
    result_model = create_model(
        tool.output_model.__name__ + "McpResult",
        __base__=tool.output_model,
        document_id=(str, ...),
    )

    async def run(api: ApiClient, args) -> dict | ErrorAnswer:
        data = await api.get(f"/documents/{args.document_id}/record", tool=tool.name)
        record = reviewed_view(DocumentRecord.model_validate(data["record"]))
        source = ToolSource(pages=tuple(record.pages), record=record)
        rest = args.model_dump(exclude={"document_id"}, exclude_unset=True)
        outcome = shared.invoke({tool.name: tool}, tool.name, rest, source)
        if not outcome.ok:
            return ErrorAnswer(outcome.error.kind, outcome.error.message)
        result = _document_cites(
            args.document_id, outcome.output.model_dump(mode="json")
        )
        result["document_id"] = args.document_id
        return _cited(result, _citations(tool.name, args.document_id, result))

    return ServerTool(
        name=tool.name,
        description=tool.description + " Give the document's id as document_id.",
        input_model=input_model,
        output_model=_answer_model(
            input_model.__name__.replace("Input", "Answer"), result_model
        ),
        run=run,
    )


TOOLS: dict[str, ServerTool] = {
    t.name: t
    for t in (
        FIND_PATIENTS,
        LIST_DOCUMENTS,
        *(_document_tool(t) for t in shared.TOOLS.values()),
    )
}


async def call(api: ApiClient, name: str, arguments: Any) -> Answer:
    """Check a client's call against the tool's schema and run it."""
    tool = TOOLS.get(name)
    if tool is None:
        known = ", ".join(TOOLS)
        return Answer(
            str(name)[:64], error=ErrorAnswer("unknown_tool", f"tools: {known}")
        )
    if not isinstance(arguments, dict):
        return Answer(
            name,
            error=ErrorAnswer("malformed_arguments", "arguments are not an object"),
        )
    try:
        args = tool.input_model.model_validate(arguments)
    except ValidationError as e:
        return Answer(name, error=ErrorAnswer("malformed_arguments", _describe(e)))
    try:
        out = await tool.run(api, args)
    except ApiError as e:
        return Answer(name, error=_api_error(e), api_status=e.status)
    except UNAVAILABLE as e:
        log.warning(
            "tool %s could not read its answer: %s",
            name,
            type(e).__name__,
            extra={"obs": {"event": "mcp_tool_unavailable", "tool": name,
                           "error_type": type(e).__name__}},
        )  # fmt: skip
        return Answer(
            name,
            error=ErrorAnswer(
                "unavailable", "the API could not be reached or its answer read"
            ),
        )
    if isinstance(out, ErrorAnswer):
        return Answer(name, error=out, api_status=200)
    return Answer(name, structured=out, api_status=200)


def log_fields(answer: Answer, started: float) -> dict:
    """What may be logged about a call: the tool and its outcome, no value."""
    fields = {
        "event": "mcp_tool_called",
        "tool": answer.tool,
        "ok": answer.ok,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if answer.error is not None:
        fields["error_kind"] = answer.error.kind
    if answer.api_status is not None:
        fields["status"] = answer.api_status
    return fields
