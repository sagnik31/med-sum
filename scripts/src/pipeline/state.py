"""
The pipeline graph's state: what the nodes read and write as a document
goes through its nodes (pipeline/nodes.py).

The state is checkpointed after every node, so it holds document content
(transcriptions, the parse, the record) but never page images: a page is
kept as its number, hash, size and dimensions, and the transcribe node
renders it again from the stored file when it needs the pixels.

Nodes return only the keys they change. Every key overwrites, except
`transcriptions`, which merges by page number so that one page can be
transcribed (or re-transcribed by a repair) on its own.

Values are the record's Pydantic models. The checkpointer only restores
the types listed by checkpoint_types(), which are found by walking these
annotations, so a type added here is allowed without a second list to
keep in step.
"""

import dataclasses
import enum
import typing
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field

from record.common import Strict
from record.document import DocumentRecord, PageTranscription
from record.printed import DocumentType, LabReportParse, PrescriptionParse
from validators import Finding
from validators.patient import ExpectedPatient

ModelRole = Literal["primary", "fallback"]
EndState = Literal["verified", "needs_review", "failed"]


class PageImage(Strict):
    """A rendered page as the state keeps it: never the image itself."""

    page: int = Field(ge=1)
    sha256: str
    size_bytes: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


def merge_pages(
    current: list[PageTranscription] | None, update: list[PageTranscription] | None
) -> list[PageTranscription]:
    """
    Reducer for `transcriptions`: pages from the update replace the pages
    with the same number, the others stay, and the result is in page order.
    """
    by_page = {p.page: p for p in current or []}
    by_page.update({p.page: p for p in update or []})
    return [by_page[n] for n in sorted(by_page)]


class GraphState(TypedDict):
    # Given when the run starts -------------------------------------------
    document_id: str
    # The stored upload (absolute path). A path, never file content; it is
    # stored in the checkpoint and never logged.
    file_path: NotRequired[str]
    # The uploader's name, kept on the record (SourceDocument.uploaded_by).
    uploaded_by: NotRequired[str | None]
    # Who the document was uploaded for, for validator 9 (patient match).
    # Until patients are records of their own, only the uploader's name.
    expected_patient: NotRequired[ExpectedPatient | None]

    # classify ------------------------------------------------------------
    doc_type: NotRequired[DocumentType | None]

    # transcribe ----------------------------------------------------------
    # Pages in the file, which can be more than are transcribed (page cap).
    pages_in_file: NotRequired[int]
    pages: NotRequired[list[PageImage]]
    # Numbered lines per page; each carries the PDF's own text for that
    # page (text_layer) when the file has one.
    transcriptions: NotRequired[Annotated[list[PageTranscription], merge_pages]]
    # Pages whose transcription hit the token cap on every model; each is
    # in `transcriptions` with no lines (signal LC-02).
    capped_pages: NotRequired[list[int]]

    # parse and repair ----------------------------------------------------
    parse: NotRequired[LabReportParse | PrescriptionParse | None]
    # Why the last parse reply was refused (not JSON, or not the schema),
    # for the repair prompt; None after a parse that fit.
    parse_error: NotRequired[str | None]
    # Parse attempts so far with the model in use (1 = the first parse).
    parse_attempts: NotRequired[int]
    model_role: NotRequired[ModelRole]

    # validate ------------------------------------------------------------
    record: NotRequired[DocumentRecord | None]
    findings: NotRequired[list[Finding]]

    # render --------------------------------------------------------------
    # The insight page as the app shows it today (step 10 replaces it).
    insight_html: NotRequired[str | None]

    # end -----------------------------------------------------------------
    end_state: NotRequired[EndState | None]
    # Why the run failed, for the database; never logged.
    error: NotRequired[str | None]


def initial_state(
    document_id: str,
    *,
    file_path: str | None = None,
    uploaded_by: str | None = None,
    expected_patient: ExpectedPatient | None = None,
) -> GraphState:
    """The state a run starts from."""
    state = GraphState(
        document_id=str(document_id),
        uploaded_by=uploaded_by,
        expected_patient=expected_patient,
        transcriptions=[],
        findings=[],
        parse_attempts=0,
        model_role="primary",
    )
    if file_path is not None:
        state["file_path"] = str(file_path)
    return state


# -------------------------------------------------------------------
# Types the checkpointer may restore
# -------------------------------------------------------------------
def _classes_in(annotation, found: dict[str, type]) -> None:
    """Every model, enum and dataclass an annotation can hold, recursively."""
    for arg in typing.get_args(annotation):
        _classes_in(arg, found)
    origin = typing.get_origin(annotation)
    if origin is not None or not isinstance(annotation, type):
        return
    key = f"{annotation.__module__}.{annotation.__qualname__}"
    if key in found:
        return
    if issubclass(annotation, BaseModel):
        found[key] = annotation
        for field in annotation.model_fields.values():
            _classes_in(field.annotation, found)
    elif issubclass(annotation, enum.Enum):
        found[key] = annotation
    elif dataclasses.is_dataclass(annotation):
        found[key] = annotation
        for hint in typing.get_type_hints(annotation).values():
            _classes_in(hint, found)


def checkpoint_types() -> list[tuple[str, str]]:
    """
    (module, class name) of every model, enum and dataclass the state can
    hold, nested ones included: the checkpointer's msgpack allowlist.
    """
    found: dict[str, type] = {}
    for hint in typing.get_type_hints(GraphState, include_extras=True).values():
        _classes_in(hint, found)
    return sorted((cls.__module__, cls.__qualname__) for cls in found.values())
