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
from validators.text import is_blank, is_heading


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
    findings = []
    lines = medicine_lines(record)
    cited = {
        m.id: {(m.printed.citation.page, n) for n in range(
            m.printed.citation.first_line, m.printed.citation.last_line + 1)}
        for m in record.medications
    }  # fmt: skip
    all_cited = set().union(*cited.values()) if cited else set()
    for page, n in lines:
        if (page, n) not in all_cited:
            findings.append(Finding(
                validator=Validator.MEDICINE_COVERAGE, severity=Severity.ERROR,
                message="this line holds a medicine that is not in the record",
                page=page, lines=[n],
                details={"line": record.pages[page - 1].lines[n - 1]}))  # fmt: skip
    medicine_line_set = set(lines)
    for m in record.medications:
        if not cited[m.id] & medicine_line_set:
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


def check_result_coverage(record: DocumentRecord) -> list[Finding]:
    """
    Every data row of a results table is cited by a result. A dropped row
    would otherwise reach the summary as nothing at all, so it is an error
    (a re-parse can add it), like a dropped medicine line.
    """
    cited = {
        (o.printed.citation.page, n)
        for o in record.observations
        for n in range(o.printed.citation.first_line, o.printed.citation.last_line + 1)
    }
    return [
        Finding(
            validator=Validator.RESULT_COVERAGE,
            severity=Severity.ERROR,
            message="this row of a results table has no result in the record",
            page=page,
            lines=[n],
            details={"line": record.pages[page - 1].lines[n - 1]},
        )
        for page, n in result_rows(record)
        if (page, n) not in cited
    ]


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
        used.update((c.page, n) for n in range(c.first_line, c.last_line + 1))
    for o in record.observations:
        if o.printed.panel is not None:
            heading = _heading_above(record, o.printed.citation, o.printed.panel)
            if heading:
                used.add(heading)

    unused = []
    for p in record.pages:
        for n, line in enumerate(p.lines, start=1):
            if (p.page, n) in used or is_blank(line) or _is_table_furniture(p.lines, n):
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


def _is_table_furniture(lines: list[str], n: int) -> bool:
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
