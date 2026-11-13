"""
Tools that read the transcription: the page lines every citation points at.
"""

from pydantic import Field, model_validator

from tools.base import Tool, ToolError, ToolInput, ToolOutput, ToolSource

# Most lines one call returns: a results table's rows with room around
# them, and short enough that a few calls fit a small model's context.
MAX_LINES = 40


class GetLinesInput(ToolInput):
    page: int = Field(ge=1, description="Page number, from 1.")
    first: int = Field(ge=1, description="First line to return, from 1.")
    last: int = Field(ge=1, description="Last line to return (inclusive).")

    @model_validator(mode="after")
    def _ordered(self):
        if self.last < self.first:
            raise ValueError("last must not be before first")
        return self


class Line(ToolOutput):
    number: int
    text: str


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
        numbers = ", ".join(str(p.page) for p in source.pages) or "none"
        return ToolError("not_found", f"no page {args.page}; pages: {numbers}")
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
        cite=f"p{args.page}:{args.first}-{last}",
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
