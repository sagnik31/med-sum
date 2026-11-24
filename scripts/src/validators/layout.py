"""
Validators 5, 6, 6b and 8, which read the layout of the transcription:
where a remark sits, which lines are medicines, which table rows are lab
results, and which lines no item uses.
"""

import re

from derive.build import same_text
from record.common import Citation
from record.document import DocumentRecord, Observation
from record.printed import DocumentType, RemarkScope
from validators.fields import item_citations
from validators.findings import Finding, Severity, Validator
from validators.text import cited_numbers, is_blank, is_heading, normalise


def _position(c: Citation) -> tuple[int, int]:
    return (c.page, c.first_line)


def _end(c: Citation) -> tuple[int, int]:
    return (c.page, c.last_line)


def _lines_between(
    record: DocumentRecord, start: tuple[int, int], end: tuple[int, int]
):
    """Lines strictly after `start` and strictly before `end`, in reading order."""
    for p in record.pages:
        for n, line in enumerate(p.lines, start=1):
            if start < (p.page, n) < end:
                yield line


# -------------------------------------------------------------------
# 5: remarks sit by what they're on: a remark belongs to a test only on its
# row or directly under it (see docs/adr/0003-printed-and-derived-values.md)
# -------------------------------------------------------------------
def check_remark_placement(record: DocumentRecord) -> list[Finding]:
    """
    A test remark is on its test's row or directly under it (only blank
    lines between). A panel remark is under the panel: after its first
    result, with no other panel's result and no heading in between.

    A comment printed right under a panel's last row passes either way;
    whether it is on the test or the panel is the parse step's reading,
    which no layout check can settle.
    """
    findings = []
    by_id = {o.id: o for o in record.observations}

    def add(remark, message):
        c = remark.printed.citation
        findings.append(Finding(
            validator=Validator.REMARK_PLACEMENT, severity=Severity.ERROR,
            message=message, item_id=remark.id, field="scope", page=c.page,
            lines=[c.first_line]))  # fmt: skip

    for remark in record.remarks:
        p = remark.printed
        if p.scope is RemarkScope.TEST:
            target = by_id.get(remark.derived.observation_id)
            if target is None:
                add(remark, f"is on test {p.test_name!r}, but no single result "
                    "has that name")  # fmt: skip
            elif not _directly_under(record, target.printed.citation, p.citation):
                add(remark, f"is on test {p.test_name!r}, but is not on that row or "
                    "directly under it")  # fmt: skip
        elif p.scope is RemarkScope.PANEL:
            in_panel = [o for o in record.observations
                        if same_text(o.printed.panel, p.panel)]  # fmt: skip
            if not in_panel:
                add(remark, f"is on panel {p.panel!r}, which has no results")
            elif not _under_panel(record, in_panel, remark.printed.citation):
                add(remark, f"is on panel {p.panel!r}, but is not printed under it")
    return findings


def _directly_under(record: DocumentRecord, row: Citation, remark: Citation) -> bool:
    if row.page != remark.page:
        return False
    if row.first_line <= remark.first_line <= row.last_line:
        return True
    if remark.first_line < row.first_line:
        return False
    return all(
        is_blank(line) for line in _lines_between(record, _end(row), _position(remark))
    )


def _under_panel(
    record: DocumentRecord, in_panel: list[Observation], remark: Citation
) -> bool:
    first = min(_position(o.printed.citation) for o in in_panel)
    last = max(_end(o.printed.citation) for o in in_panel)
    here = _position(remark)
    if here < first:
        return False
    if here <= last:
        return True
    panel = in_panel[0].printed.panel
    for o in record.observations:
        if last < _position(o.printed.citation) < here and not same_text(
            o.printed.panel, panel
        ):
            return False
    return not any(is_heading(line) for line in _lines_between(record, last, here))


# -------------------------------------------------------------------
# 6: no medicine invented, none dropped
# -------------------------------------------------------------------
FORMS = (
    "tab", "tabs", "tablet", "cap", "caps", "capsule", "syp", "syrup", "inj",
    "injection", "oint", "ointment", "drop", "drops", "gel", "cream", "susp",
    "suspension", "sachet", "inhaler", "spray", "lotion", "powder", "soln",
    "solution", "liq",
)  # fmt: skip
# A line starting with a dosage form, after any table bar or item number.
_MEDICINE_LINE = re.compile(
    rf"^[\s|]*(?:\d+\s*[.)]?\s*\|?\s*)?(?:{'|'.join(FORMS)})\b", re.IGNORECASE
)
_RX_HEADING = re.compile(
    r"^#*\s*(?:rx|℞|prescription|medicines?|medications?)\s*:?\s*$", re.IGNORECASE
)
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}")


def medicine_lines(record: DocumentRecord) -> list[tuple[int, int]]:
    """
    (page, line) of each line that holds a medicine: one starting with a
    dosage form (Tab, Cap, Syp, Inj, ...), or a table row under an Rx
    heading.
    """
    return [(p.page, n) for p in record.pages for n in medicine_line_numbers(p.lines)]


def medicine_line_numbers(lines: list[str]) -> list[int]:
    """medicine_lines for one page's lines: their line numbers."""
    found = []
    in_rx = False
    for n, line in enumerate(lines, start=1):
        if is_heading(line) or _RX_HEADING.match(line):
            in_rx = bool(_RX_HEADING.match(line))
            continue
        if _MEDICINE_LINE.match(line) or (in_rx and _is_table_row(lines, n)):
            found.append(n)
    return found


def _is_table_row(lines: list[str], n: int) -> bool:
    """A table data row: not the header (followed by a separator), not a separator."""
    line = lines[n - 1]
    if not line.lstrip().startswith("|") or _TABLE_SEPARATOR.match(line):
        return False
    followed_by_separator = n < len(lines) and _TABLE_SEPARATOR.match(lines[n])
    return not followed_by_separator


def check_medicine_coverage(record: DocumentRecord) -> list[Finding]:
    """
    Every medicine line is cited by a medicine, and each medicine cites one
    (with any lines that continue it). One that cites several is an error
    and covers none, as a result citing several rows is: validators 1 and 2
    look for its parts anywhere in the cited lines, so two medicines could
    trade doses, or one hide another's line.
    See docs/adr/0004-deterministic-checks-decide-routing.md.
    """
    findings = []
    lines = medicine_lines(record)
    medicine_line_set = set(lines)
    spanned = {
        m.id: [
            (m.printed.citation.page, n)
            for n in cited_numbers(record, m.printed.citation)
            if (m.printed.citation.page, n) in medicine_line_set
        ]
        for m in record.medications
    }
    all_cited = set()
    for m in record.medications:
        on = spanned[m.id]
        if len(on) > 1:
            findings.append(Finding(
                validator=Validator.MEDICINE_COVERAGE, severity=Severity.ERROR,
                message=f"medicine {m.printed.name!r} cites {len(on)} lines that "
                        "each hold a medicine; cite only its own",
                item_id=m.id, field="citation", page=on[0][0],
                lines=[n for _, n in on]))  # fmt: skip
        else:
            all_cited.update(on)
    for page, n in lines:
        if (page, n) not in all_cited:
            findings.append(Finding(
                validator=Validator.MEDICINE_COVERAGE, severity=Severity.ERROR,
                message="this line holds a medicine that is not in the record",
                page=page, lines=[n],
                details={"line": record.pages[page - 1].lines[n - 1]}))  # fmt: skip
    for m in record.medications:
        if not spanned[m.id]:
            c = m.printed.citation
            findings.append(Finding(
                validator=Validator.MEDICINE_COVERAGE, severity=Severity.ERROR,
                message=f"medicine {m.printed.name!r} is not cited on a line that "
                        "holds a medicine", item_id=m.id, field="citation",
                page=c.page, lines=[c.first_line]))  # fmt: skip
    return findings


# -------------------------------------------------------------------
# 6b: no lab result dropped
# -------------------------------------------------------------------
# A table is a results table when a header cell names the result column.
_RESULT_HEADER = re.compile(
    r"\b(?:results?|values?|observed|observations?|readings?|findings?)\b",
    re.IGNORECASE,
)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def result_rows(record: DocumentRecord) -> list[tuple[int, int]]:
    """
    (page, line) of each data row of a results table: a Markdown table
    whose header row (the row above its separator) has a result column
    ("Result", "Observed Value", ...). A row with nothing after its first
    cell is a heading inside the table, not a result. Results printed as
    free text rather than a table are not listed: their lines carry no
    shape that says "one result per line", so they stay with coverage.
    """
    if record.document.type is not DocumentType.LAB_REPORT:
        return []
    return [(p.page, n) for p in record.pages for n in results_table_rows(p.lines)]


def results_table_rows(lines: list[str]) -> list[int]:
    """result_rows for one page's lines: their line numbers."""
    found = []
    in_results = False
    for n, line in enumerate(lines, start=1):
        if not line.lstrip().startswith("|"):
            in_results = False
            continue
        if _TABLE_SEPARATOR.match(line):
            continue
        if n < len(lines) and _TABLE_SEPARATOR.match(lines[n]):
            in_results = any(_RESULT_HEADER.search(c) for c in _cells(line))
            continue
        if in_results and any(_cells(line)[1:]):
            found.append(n)
    return found


_UNIT_HEADER = re.compile(r"units?(?:\s*\(s\))?|unit\(s\)|uom", re.IGNORECASE)
_NO_UNIT = {"na", "n/a", "nil", "none", "not applicable"}


def unit_cells(lines: list[str]) -> dict[int, str]:
    """
    What a results table's unit column prints on each data row: line number
    to text. A row with more or fewer cells than the header is left out,
    as its cells can't be told apart, and so is a cell that says no unit.
    """
    found = {}
    rows = set(results_table_rows(lines))
    column = width = None
    for n, line in enumerate(lines, start=1):
        if n < len(lines) and _TABLE_SEPARATOR.match(lines[n]):
            cells = _cells(line)
            at = [i for i, c in enumerate(cells) if _UNIT_HEADER.fullmatch(c)]
            column, width = (at[0] if len(at) == 1 else None), len(cells)
        elif n in rows and column is not None:
            cells = _cells(line)
            if len(cells) == width:
                cell = cells[column]
                if any(c.isalpha() or c == "%" for c in cell):
                    if cell.casefold() not in _NO_UNIT:
                        found[n] = cell
    return found


def check_result_coverage(record: DocumentRecord) -> list[Finding]:
    """
    Every data row of a results table is cited by a result, and each
    result cites one data row. A dropped row would otherwise reach the
    summary as nothing at all, so it is an error (a re-parse can add it),
    like a dropped medicine line.

    A result whose citation spans several data rows is an error too, and
    covers none of them: validators 1 and 2 look for its values anywhere
    in the cited lines, so two results citing the same pair of rows could
    trade values, and one result citing a whole table would hide every
    row it dropped. See docs/adr/0004-deterministic-checks-decide-routing.md.
    """
    rows = result_rows(record)
    data_rows = set(rows)
    cited, findings = set(), []
    for o in record.observations:
        c = o.printed.citation
        spanned = [
            (c.page, n) for n in cited_numbers(record, c) if (c.page, n) in data_rows
        ]
        if len(spanned) > 1:
            findings.append(Finding(
                validator=Validator.RESULT_COVERAGE, severity=Severity.ERROR,
                message=f"result {o.printed.test_name!r} cites {len(spanned)} rows "
                        "of a results table; a result is on one row",
                item_id=o.id, field="citation", page=c.page,
                lines=[n for _, n in spanned]))  # fmt: skip
        else:
            cited.update(spanned)
    findings += [
        Finding(
            validator=Validator.RESULT_COVERAGE,
            severity=Severity.ERROR,
            message="this row of a results table has no result in the record",
            page=page,
            lines=[n],
            details={"line": record.pages[page - 1].lines[n - 1]},
        )
        for page, n in rows
        if (page, n) not in cited
    ]
    return findings


# -------------------------------------------------------------------
# 8: coverage: printed lines no item uses, so nothing is lost silently
# -------------------------------------------------------------------
_BOILERPLATE = [
    re.compile(r"\bpage\s+\d+\s*(?:of|/)\s*\d+\b", re.IGNORECASE),
    re.compile(r"end\s+of\s+report", re.IGNORECASE),
    re.compile(r"^\s*signature\b", re.IGNORECASE),
    re.compile(
        r"^#+\s*(?:rx|℞|remarks?|pathologist'?s\s+remarks?|advice|comments?|notes?|"
        r"interpretation)\s*:?\s*$",
        re.IGNORECASE,
    ),
]


def unused_lines(record: DocumentRecord) -> list[tuple[int, int]]:
    """
    (page, line) of each non-blank line that no item cites, that isn't the
    heading of a panel in use, and isn't known boilerplate (table headers
    and separators, running headers, end-of-report and signature lines,
    section headings such as Rx and Remarks).
    """
    used = set()
    for _, _, c in item_citations(record):
        used.update((c.page, n) for n in cited_numbers(record, c))
    for o in record.observations:
        if o.printed.panel is not None:
            heading = _heading_above(record, o.printed.citation, o.printed.panel)
            if heading:
                used.add(heading)

    unused = []
    for p in record.pages:
        for n, line in enumerate(p.lines, start=1):
            if (p.page, n) in used or is_blank(line) or is_table_furniture(p.lines, n):
                continue
            if any(pattern.search(line) for pattern in _BOILERPLATE):
                continue
            unused.append((p.page, n))
    return unused


def _heading_above(record: DocumentRecord, c: Citation, panel: str):
    """The nearest line above a result that names its panel, on this page or before."""
    for p in reversed(record.pages[: c.page]):
        last = c.first_line - 1 if p.page == c.page else len(p.lines)
        for n in range(last, 0, -1):
            if same_text(p.lines[n - 1].lstrip("# "), panel) or (
                is_heading(p.lines[n - 1]) and panel in p.lines[n - 1]
            ):
                return (p.page, n)
    return None


# How deep a heading is when its line carries no "#".
_BOLD_LEVEL, _ROW_LEVEL = 7, 8
_BOLD_LINE = re.compile(r"^\s*\*\*[^*]+\*\*\s*:?\s*$")


def _heading_level(lines: list[str], n: int) -> int | None:
    """
    How deep a heading line n is: its "#"s, a line all in bold, or a row
    with nothing after its first cell inside a table. None for other lines.
    """
    line = lines[n - 1]
    if is_heading(line):
        text = line.lstrip()
        return len(text) - len(text.lstrip("#"))
    if _BOLD_LINE.match(line):
        return _BOLD_LEVEL
    if _is_table_row(lines, n):
        cells = _cells(line)
        if cells[0] and len(cells) > 1 and not any(cells[1:]):
            return _ROW_LEVEL
    return None


def under_another_heading(record: DocumentRecord, c: Citation, panel: str) -> bool:
    """
    Whether another section's heading comes between the panel named for a
    result and its row: then the row is under that heading, not the panel's
    ("Serum" named for a row printed under "Urine"). A deeper heading is a
    part of the panel, one that names the panel continues it, and one
    printed before it too is a running header: none of these end it.
    """
    above = [
        (p.lines, n)
        for p in record.pages[: c.page]
        for n in range(1, (c.first_line if p.page == c.page else len(p.lines) + 1))
    ]
    name = normalise(panel)
    named = [k for k, (ls, n) in enumerate(above) if name in normalise(ls[n - 1])]
    if not named:
        return False  # not printed above at all: the caller's finding
    headed = [k for k in named if _heading_level(*above[k]) is not None]
    at = (headed or named)[-1]
    level = _heading_level(*above[at]) or _ROW_LEVEL
    before = {normalise(ls[n - 1]) for ls, n in above[:at]}
    for ls, n in above[at + 1 :]:
        line = normalise(ls[n - 1])
        depth = _heading_level(ls, n)
        if depth is None or depth > level or line in before:
            continue
        if not any(pattern.search(line) for pattern in _BOILERPLATE):
            return True
    return False


def is_table_furniture(lines: list[str], n: int) -> bool:
    """A table's header row (the row above its separator) or a separator."""
    line = lines[n - 1]
    if _TABLE_SEPARATOR.match(line):
        return True
    return (
        line.lstrip().startswith("|")
        and n < len(lines)
        and bool(_TABLE_SEPARATOR.match(lines[n]))
    )


def check_coverage(record: DocumentRecord) -> list[Finding]:
    """Never a failure on its own: the list is shown in review."""
    return [
        Finding(
            validator=Validator.COVERAGE,
            severity=Severity.INFO,
            message="no extracted item uses this line",
            page=page,
            lines=[n],
            details={"line": record.pages[page - 1].lines[n - 1]},
        )
        for page, n in unused_lines(record)
    ]
