"""
Tools that read the transcription (the page lines every citation points
at) and the PDF's own text layer.
"""

import re
import unicodedata

from pydantic import Field, model_validator

from tools.base import Tool, ToolError, ToolInput, ToolOutput, ToolSource, cite

# Most lines one call returns: a results table's rows with room around
# them, and short enough that a few calls fit a small model's context.
MAX_LINES = 40
# Most places one search returns; a search that finds more is too broad.
MAX_HITS = 12


class Line(ToolOutput):
    number: int
    text: str


# -------------------------------------------------------------------
# get_lines
# -------------------------------------------------------------------
class GetLinesInput(ToolInput):
    page: int = Field(ge=1, description="Page number, from 1.")
    first: int = Field(ge=1, description="First line to return, from 1.")
    last: int = Field(ge=1, description="Last line to return (inclusive).")

    @model_validator(mode="after")
    def _ordered(self):
        if self.last < self.first:
            raise ValueError("last must not be before first")
        return self


class GetLinesOutput(ToolOutput):
    page: int
    first: int
    last: int
    page_lines: int = Field(description="How many lines the page has.")
    lines: list[Line]
    cut: bool = Field(
        description="True when fewer lines were returned than asked for "
        "(past the page's end, or more than the limit per call)."
    )
    cite: str = Field(description="Where these lines are, as pP:first-last.")

    def sizes(self) -> dict[str, int]:
        return {"lines_returned": len(self.lines)}


def _get_lines(source: ToolSource, args: GetLinesInput) -> GetLinesOutput | ToolError:
    page = source.page(args.page)
    if page is None:
        return ToolError(
            "not_found", f"no page {args.page}; pages: {source.page_numbers}"
        )
    total = len(page.lines)
    if args.first > total:
        return ToolError(
            "not_found", f"page {args.page} has {total} lines; first is past its end"
        )
    last = min(args.last, total, args.first + MAX_LINES - 1)
    lines = [
        Line(number=n, text=page.lines[n - 1]) for n in range(args.first, last + 1)
    ]
    return GetLinesOutput(
        page=args.page,
        first=args.first,
        last=last,
        page_lines=total,
        lines=lines,
        cut=last < args.last,
        cite=cite(args.page, args.first, last),
    )


GET_LINES = Tool(
    name="get_lines",
    description=(
        "Return lines first..last (inclusive, numbered from 1) of one page of "
        "the document's transcription, each with its line number, so a "
        "citation can be checked or corrected. At most "
        f"{MAX_LINES} lines per call."
    ),
    input_model=GetLinesInput,
    output_model=GetLinesOutput,
    run=_get_lines,
)


# -------------------------------------------------------------------
# search_transcription
# -------------------------------------------------------------------
def _folded(text: str) -> str:
    """NFKC, case folded, spacing collapsed: how a search compares text."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


class SearchInput(ToolInput):
    text: str = Field(
        min_length=2,
        max_length=120,
        description="Text to look for, e.g. a value or a test name. Case and "
        "spacing are ignored.",
    )
    page: int | None = Field(
        default=None, ge=1, description="Only this page; leave out for every page."
    )


class Hit(ToolOutput):
    page: int
    line: int
    text: str
    cite: str


class SearchOutput(ToolOutput):
    hits: list[Hit]
    more: bool = Field(description=f"True when more than {MAX_HITS} lines matched.")

    def sizes(self) -> dict[str, int]:
        return {"hits": len(self.hits)}


def _search(source: ToolSource, args: SearchInput) -> SearchOutput | ToolError:
    needle = _folded(args.text)
    if len(needle) < 2:
        return ToolError("malformed_arguments", "text: needs at least 2 characters")
    pages = source.pages
    if args.page is not None:
        page = source.page(args.page)
        if page is None:
            return ToolError(
                "not_found", f"no page {args.page}; pages: {source.page_numbers}"
            )
        pages = (page,)
    hits = [
        Hit(page=p.page, line=n, text=line, cite=cite(p.page, n))
        for p in pages
        for n, line in enumerate(p.lines, start=1)
        if needle in _folded(line)
    ]
    return SearchOutput(hits=hits[:MAX_HITS], more=len(hits) > MAX_HITS)


SEARCH_TRANSCRIPTION = Tool(
    name="search_transcription",
    description=(
        "Find the lines of the transcription that contain some text (case "
        "and spacing ignored), on every page or one page. Returns each "
        f"line's page, number and text, at most {MAX_HITS}. Use it to find "
        "where a value or test name is printed."
    ),
    input_model=SearchInput,
    output_model=SearchOutput,
    run=_search,
)


# -------------------------------------------------------------------
# get_text_layer
# -------------------------------------------------------------------
class TextLayerInput(ToolInput):
    page: int = Field(ge=1, description="Page number, from 1.")
    first: int = Field(default=1, ge=1, description="First line of the text layer.")
    last: int | None = Field(
        default=None, ge=1, description=f"Last line; at most {MAX_LINES} per call."
    )

    @model_validator(mode="after")
    def _ordered(self):
        if self.last is not None and self.last < self.first:
            raise ValueError("last must not be before first")
        return self


class TextLayerOutput(ToolOutput):
    page: int
    has_text_layer: bool = Field(
        description="False for an image or a scan: there is no text layer, "
        "so the transcription is the only reading of the page."
    )
    layer_lines: int = Field(description="How many lines the text layer has.")
    lines: list[Line]
    cut: bool

    def sizes(self) -> dict[str, int]:
        return {"lines_returned": len(self.lines)}


def _text_layer(
    source: ToolSource, args: TextLayerInput
) -> TextLayerOutput | ToolError:
    page = source.page(args.page)
    if page is None:
        return ToolError(
            "not_found", f"no page {args.page}; pages: {source.page_numbers}"
        )
    if not page.text_layer:
        return TextLayerOutput(
            page=args.page, has_text_layer=False, layer_lines=0, lines=[], cut=False
        )
    layer = page.text_layer.splitlines()
    total = len(layer)
    if args.first > total:
        return ToolError(
            "not_found",
            f"the text layer of page {args.page} has {total} lines; "
            "first is past its end",
        )
    asked = args.last if args.last is not None else total
    last = min(asked, total, args.first + MAX_LINES - 1)
    lines = [Line(number=n, text=layer[n - 1]) for n in range(args.first, last + 1)]
    return TextLayerOutput(
        page=args.page,
        has_text_layer=True,
        layer_lines=total,
        lines=lines,
        cut=last < asked,
    )


GET_TEXT_LAYER = Tool(
    name="get_text_layer",
    description=(
        "Return the PDF's own text for one page (its text layer, which the "
        "transcription is checked against), as numbered lines, at most "
        f"{MAX_LINES} per call. Its line numbers are not the transcription's. "
        "Pages from images or scans have no text layer."
    ),
    input_model=TextLayerInput,
    output_model=TextLayerOutput,
    run=_text_layer,
)
