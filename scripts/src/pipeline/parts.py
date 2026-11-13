"""
The parse in parts: a document is parsed one part at a time, and the parts'
replies are merged in code into the one parse the record is built from.

A model's reply has a size ceiling (its output cap), and a parse reply
grows with every result it holds: a whole multi-page lab report in one
call can run out of room and come back cut off. So each page is a part of
its own, and a page with more rows (results-table rows and medicine lines)
than MAX_ROWS_PER_PART fit is cut into parts between rows. A part that
starts inside a table is shown the lines it needs to read its rows: the
nearest heading above (the panel's name) and the table's header row and
separator, with their own line numbers, so citations still point at the
page.

Merging is code's job, as item ids and remark links already are: the
patient's and the document's printed details take each field from the
first part that has it (a page header repeats them), clinicians are
listed once each, and every list of items is the parts' lists in order.
See docs/adr/0033-parse-per-page-in-parts.md.
"""

import math
import unicodedata

from pydantic import Field

from record.common import Provenance, Strict
from record.document import PageTranscription
from record.printed import DocumentType, LabReportParse, PrescriptionParse
from validators.layout import medicine_line_numbers, results_table_rows
from validators.text import is_blank, is_heading

Parse = LabReportParse | PrescriptionParse

# Rows one parse call is given. Measured live (qwen3.5:4b, a 26-result
# lab report in one call): 5,084 output tokens, about 195 per result with
# the patient and document details and three remarks, so the 8,192-token
# cap is reached near 40 rows. 25 leaves room for wordier rows (long
# reference ranges) and a model that writes more per row.
MAX_ROWS_PER_PART = 25


class ParsePart(Strict):
    """Lines of one page that one parse call reads."""

    index: int = Field(ge=0)
    page: int = Field(ge=1)
    first_line: int = Field(ge=1)
    last_line: int = Field(ge=0)  # 0 for a page with no lines
    # Lines above the part, shown so its rows can be read: the panel's
    # heading and the table's header row and separator.
    context: list[int] = []

    def holds(self, page: int, line: int) -> bool:
        return page == self.page and self.first_line <= line <= self.last_line


class PartResult(Strict):
    """One part's latest parse reply, as the graph state keeps it."""

    index: int = Field(ge=0)
    # The reply as the model wrote it (None if it was cut off), which a
    # repair shows the model again.
    reply: str | None = None
    parse: LabReportParse | PrescriptionParse | None = None
    # Why the reply was refused (not JSON, off the schema, cut off).
    error: str | None = None
    # What produced the parse: its items are stamped with it.
    provenance: Provenance | None = None
    model_role: str | None = None
    # A call's prompt may have been cut to fit the context window.
    truncated: bool = False


def _rows(page: PageTranscription, doc_type: DocumentType) -> list[int]:
    """Line numbers of the page's rows: results-table rows or medicine lines."""
    if doc_type is DocumentType.LAB_REPORT:
        return results_table_rows(page.lines)
    return medicine_line_numbers(page.lines)


def plan(
    pages: list[PageTranscription],
    doc_type: DocumentType,
    max_rows: int | None = None,
) -> list[ParsePart]:
    """The parts a document is parsed in, in reading order."""
    max_rows = max_rows or MAX_ROWS_PER_PART
    parts: list[ParsePart] = []
    # A page with no lines (its transcription was cut off) has nothing to
    # parse; a document with no lines at all still gets its one call.
    with_lines = [p for p in pages if p.lines] or pages[:1]
    for page in with_lines:
        rows = _rows(page, doc_type)
        cuts = _cuts(rows, max_rows)
        starts = [1] + [_start_of(page.lines, line) for line in cuts]
        ends = [s - 1 for s in starts[1:]] + [len(page.lines)]
        for start, end in zip(starts, ends):
            parts.append(
                ParsePart(
                    index=len(parts),
                    page=page.page,
                    first_line=start,
                    last_line=end,
                    context=_context(page.lines, start) if start > 1 else [],
                )
            )
    return parts


def _cuts(rows: list[int], max_rows: int) -> list[int]:
    """The rows a page's later parts start at: even shares of max_rows or fewer."""
    if len(rows) <= max_rows:
        return []
    n = math.ceil(len(rows) / max_rows)
    size = math.ceil(len(rows) / n)
    return [rows[k * size] for k in range(1, n)]


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _line(lines: list[str], n: int) -> str:
    """Line n (1-based), or "" outside the page."""
    return lines[n - 1] if 1 <= n <= len(lines) else ""


def _heading_above(lines: list[str], n: int) -> int | None:
    """The heading line just above line n, across blank lines only."""
    n -= 1
    while n >= 1 and is_blank(lines[n - 1]):
        n -= 1
    return n if n >= 1 and is_heading(lines[n - 1]) else None


def _table_top(lines: list[str], n: int) -> int:
    """The first line of the table that line n is in."""
    while n > 1 and _is_table_line(lines[n - 2]):
        n -= 1
    return n


def _start_of(lines: list[str], row: int) -> int:
    """
    Where a part that begins at `row` starts. At a table's first data row
    it starts at the table's header (and the heading above it), so it
    needs no context; in the middle of a table it starts at the row itself.
    """
    top = _table_top(lines, row)
    if top != row - 2:  # not the first row under a header and separator
        return row
    heading = _heading_above(lines, top)
    return heading if heading is not None else top


def _context(lines: list[str], start: int) -> list[int]:
    """The heading and table header a part starting at `start` needs, if any."""
    if not _is_table_line(_line(lines, start)):
        return []
    top = _table_top(lines, start)
    if top == start:
        return []
    context = [n for n in (top, top + 1) if n < start]
    heading = _heading_above(lines, top)
    return ([heading] if heading is not None else []) + context


def part_text(pages: list[PageTranscription], part: ParsePart) -> str:
    """The numbered lines a part's parse call reads, under its page heading."""
    page = pages[part.page - 1]
    shown = [*part.context, *range(part.first_line, part.last_line + 1)]
    body = "\n".join(f"{n}| {page.lines[n - 1]}" for n in shown)
    return f"=== Page {page.page} ===\n{body}"


# -------------------------------------------------------------------
# Merging the parts' parses
# -------------------------------------------------------------------
def _key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def merge(parses: list[Parse]) -> Parse:
    """One parse from the parts' parses, in part order."""
    if not parses:
        raise ValueError("there are no parts to merge")
    first = parses[0]
    merged = first.model_copy(deep=True)
    for name in type(first.patient).model_fields:
        setattr(merged.patient, name, _first(parses, "patient", name))
    for name in type(first.details).model_fields:
        if name != "clinicians":
            setattr(merged.details, name, _first(parses, "details", name))
    seen, clinicians = set(), []
    for p in parses:
        for c in p.details.clinicians:
            if _key(c.text) not in seen:
                seen.add(_key(c.text))
                clinicians.append(c.model_copy(deep=True))
    merged.details.clinicians = clinicians
    for name in type(first).model_fields:
        if name in ("patient", "details"):
            continue
        setattr(
            merged,
            name,
            [item.model_copy(deep=True) for p in parses for item in getattr(p, name)],
        )
    return merged


def _first(parses: list[Parse], holder: str, name: str):
    """A printed detail from the first part that has it."""
    for p in parses:
        value = getattr(getattr(p, holder), name)
        if value is not None:
            return value.model_copy(deep=True)
    return None
