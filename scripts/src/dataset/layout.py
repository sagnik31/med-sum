"""
The building blocks of a synthetic document, and how a page becomes the
expected transcription (Markdown lines) and the HTML that is printed.

Both come from the same blocks, so the printed page and the expected
transcription can't disagree. Items (a lab result, a medicine, a header
field...) are attached to the line they are printed on; the answer key
cites that line.
"""

import html
import re
from dataclasses import dataclass, field, replace
from datetime import date

# -------------------------------------------------------------------
# Items: what the answer key records about a printed line
# -------------------------------------------------------------------
AUTO = object()  # "work it out from the printed text" (simple cases only)
_PLAIN_NUMBER = re.compile(r"\d+(\.\d+)?")
_PLAIN_RANGE = re.compile(r"(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Range:
    """A printed reference range and the bounds the key expects code to parse."""

    text: str
    low: float | None = None
    high: float | None = None
    label: str | None = None


def rng(text: str) -> Range:
    """A plain 'low - high' range; anything else is written out with Range()."""
    m = _PLAIN_RANGE.fullmatch(text)
    if not m:
        raise ValueError(f"not a plain range, use Range(): {text!r}")
    return Range(text, float(m[1]), float(m[2]))


@dataclass(frozen=True)
class LabTest:
    """One lab result. `num` and `computed` default to the obvious reading."""

    name: str
    value: str
    unit: str | None
    ranges: tuple[Range, ...] = ()
    flag: str | None = None  # printed flag
    num: object = AUTO  # expected value_num
    computed: object = AUTO  # expected flag_computed
    analyte: object = AUTO
    # Expected conversion to the analyte's canonical unit (the unit table of
    # derive/units.py), written by hand; None when printed in that unit.
    converted: dict | None = None
    panel: str | None = None  # set by the panel the test is printed in

    def value_num(self) -> float | None:
        if self.num is not AUTO:
            return self.num
        return float(self.value) if _PLAIN_NUMBER.fullmatch(self.value) else None

    def flag_computed(self) -> str | None:
        """Low/normal/high only when the value is a number and one range applies."""
        if self.computed is not AUTO:
            return self.computed
        num = self.value_num()
        if num is None or len(self.ranges) != 1:
            return None
        r = self.ranges[0]
        if r.low is None and r.high is None:
            return None
        if r.low is not None and num < r.low:
            return "low"
        if r.high is not None and num > r.high:
            return "high"
        return "normal"


@dataclass(frozen=True)
class HeaderField:
    """A header value; `path` is where it goes, e.g. patient.name, details.report_date."""

    path: str
    value: str
    on: date | None = None  # the date the key expects code to parse


@dataclass(frozen=True)
class Remark:
    text: str
    kind: str  # advice | comment
    scope: str  # test | panel | document
    test_name: str | None = None
    panel: str | None = None


@dataclass(frozen=True)
class Condition:
    text: str
    kind: str  # diagnosis | provisional_diagnosis | impression | history


@dataclass(frozen=True)
class Vital:
    name: str
    value: str
    unit: str | None
    kind: str
    num: float | None = None
    systolic: float | None = None
    diastolic: float | None = None


@dataclass(frozen=True)
class Allergy:
    text: str
    substance: str | None = None
    reaction: str | None = None
    no_known_allergies: bool = False


@dataclass(frozen=True)
class Medicine:
    form: str | None
    name: str
    strength: str | None
    dose: str | None
    frequency: str | None
    duration: str | None
    instructions: str | None
    route: str | None = None
    # Expected derived values.
    schedule: dict | None = None
    duration_days: int | None = None

    def cell(self) -> str:
        return " ".join(p for p in (self.form, self.name, self.strength) if p)


# -------------------------------------------------------------------
# Blocks
# -------------------------------------------------------------------
@dataclass
class Line:
    """A line of text (a paragraph on the page)."""

    text: str
    items: list = field(default_factory=list)
    style: str = ""  # extra CSS class

    def markdown(self) -> list[tuple[str, list]]:
        return [(self.text, self.items)]

    def html(self) -> str:
        cls = f' class="{self.style}"' if self.style else ""
        return f"<p{cls}>{html.escape(self.text)}</p>"


@dataclass
class Title(Line):
    def markdown(self):
        return [(f"# {self.text}", self.items)]

    def html(self):
        return f"<h1>{html.escape(self.text)}</h1>"


@dataclass
class Heading(Line):
    def markdown(self):
        return [(f"## {self.text}", self.items)]

    def html(self):
        return f"<h2>{html.escape(self.text)}</h2>"


@dataclass
class Row:
    cells: list[str]
    items: list = field(default_factory=list)


@dataclass
class Table:
    header: list[str]
    rows: list[Row]
    header_items: list = field(default_factory=list)  # items printed in the header row
    style: str = ""  # extra CSS class

    def markdown(self):
        def line(cells):
            return "| " + " | ".join(cells) + " |"

        out = [(line(self.header), self.header_items),
               (line(["---"] * len(self.header)), [])]  # fmt: skip
        return out + [(line(r.cells), r.items) for r in self.rows]

    def html(self):
        # Only the last column may wrap, so a unit like mg/dL stays whole.
        last = len(self.header) - 1

        def cell(tag, i, text):
            cls = "" if i == last else ' class="nw"'
            return f"<{tag}{cls}>{html.escape(text)}</{tag}>"

        head = "".join(cell("th", i, c) for i, c in enumerate(self.header))
        body = "".join(
            "<tr>" + "".join(cell("td", i, c) for i, c in enumerate(r.cells)) + "</tr>"
            for r in self.rows
        )
        cls = f' class="{self.style}"' if self.style else ""
        return f"<table{cls}><tr>{head}</tr>{body}</table>"


@dataclass
class Columns:
    """
    Two columns side by side. The expected transcription reads the left
    column top to bottom, then the right one, as a person would. Columns
    hold headings and lines only: PyMuPDF does not lay out a table inside
    a table cell.
    """

    left: list
    right: list

    def markdown(self):
        return [*page_markdown(self.left), ("", []), *page_markdown(self.right)]

    def html(self):
        return (
            '<table class="cols"><tr>'
            f'<td class="col" width="45%">{page_html(self.left)}</td>'
            f'<td class="col" width="55%">{page_html(self.right)}</td>'
            "</tr></table>"
        )


def page_markdown(blocks: list) -> list[tuple[str, list]]:
    """The expected transcription of a page: (line, items) pairs, blank lines between blocks."""
    lines: list[tuple[str, list]] = []
    for block in blocks:
        if lines:
            lines.append(("", []))
        lines.extend(block.markdown())
    return lines


def page_html(blocks: list) -> str:
    return "\n".join(block.html() for block in blocks)


# -------------------------------------------------------------------
# Helpers for writing documents
# -------------------------------------------------------------------
def header(label: str, path: str, value: str, on: date | None = None) -> Line:
    return Line(f"{label}: {value}", [HeaderField(path, value, on)])


def lab_panel(heading: str, tests: list[LabTest], flag_column: bool = True) -> list:
    """A panel heading and its results table, in one of two common layouts."""
    tests = [replace(t, panel=heading) for t in tests]
    if flag_column:
        cols = ["Test", "Result", "Flag", "Unit", "Reference Range"]
    else:
        cols = ["Test", "Result", "Unit", "Reference Range"]
    rows = []
    for t in tests:
        ranges = "; ".join(r.text for r in t.ranges)
        if flag_column:
            cells = [t.name, t.value, t.flag or "", t.unit or "", ranges]
        else:
            value = f"{t.value} {t.flag}" if t.flag else t.value
            cells = [t.name, value, t.unit or "", ranges]
        rows.append(Row(cells, [t]))
    return [Heading(heading), Table(cols, rows)]


def remark_line(remark: Remark, label: str | None = None) -> Line:
    text = f"{label}: {remark.text}" if label else remark.text
    return Line(text, [remark])


def conditions_line(label: str, conditions: list[Condition]) -> Line:
    return Line(f"{label}: " + ", ".join(c.text for c in conditions), list(conditions))


def vitals_line(vitals: list[Vital]) -> Line:
    parts = [" ".join(p for p in (v.name, v.value, v.unit) if p) for v in vitals]
    return Line("Vitals: " + ", ".join(parts), list(vitals))


def allergy_line(label: str, allergy: Allergy) -> Line:
    return Line(f"{label}: {allergy.text}", [allergy])


def rx_table(medicines: list[Medicine]) -> Table:
    rows = [
        Row(
            [
                str(n),
                m.cell(),
                m.dose or "",
                m.frequency or "",
                m.duration or "",
                m.instructions or "",
            ],
            [m],
        )
        for n, m in enumerate(medicines, start=1)
    ]
    return Table(
        ["#", "Medicine", "Dose", "Frequency", "Duration", "Instructions"], rows
    )


# -------------------------------------------------------------------
# Further layouts (the held-out documents)
# -------------------------------------------------------------------
def fields_line(text: str, *fields: HeaderField) -> Line:
    """One printed line holding several header fields, e.g. name, age and sex."""
    return Line(text, list(fields))


def header_table(rows: list[list[tuple[str, object, str]]]) -> Table:
    """
    Header details as a borderless key-value table: each row is (label,
    fields, printed value) pairs, fields a HeaderField, a list of them (one
    cell holding age and sex) or None; the first row is the table's header
    row.
    """

    def cells(row):
        return [c for label, _, value in row for c in (label, value)]

    def items(row):
        out = []
        for _, fields, _ in row:
            out += fields if isinstance(fields, list) else [fields] if fields else []
        return out

    first, *rest = rows
    return Table(cells(first), [Row(cells(r), items(r)) for r in rest],
                 header_items=items(first), style="kv")  # fmt: skip


def lab_lines(heading: str, tests: list[LabTest]) -> list:
    """A panel printed as one line per result, no table: 'Test: value unit (Ref: ...) flag'."""
    tests = [replace(t, panel=heading) for t in tests]
    lines = []
    for t in tests:
        text = f"{t.name}: {t.value}" + (f" {t.unit}" if t.unit else "")
        if t.ranges:
            text += " (Ref: " + "; ".join(r.text for r in t.ranges) + ")"
        if t.flag:
            text += f" {t.flag}"
        lines.append(Line(text, [t]))
    return [Heading(heading), *lines]


def lab_table_ranges_first(heading: str, tests: list[LabTest], *,
                           panel: str | None = None) -> list:  # fmt: skip
    """
    Investigation | Observed Value | Flag | Biological Reference Interval |
    Units: the range before the unit. `panel` names the panel when the
    heading isn't its name (a table continued from the previous page).
    """
    tests = [replace(t, panel=panel or heading) for t in tests]
    rows = [
        Row(
            [
                t.name,
                t.value,
                t.flag or "",
                "; ".join(r.text for r in t.ranges),
                t.unit or "",
            ],
            [t],
        )  # fmt: skip
        for t in tests
    ]
    cols = ["Investigation", "Observed Value", "Flag",
            "Biological Reference Interval", "Units"]  # fmt: skip
    return [Heading(heading), Table(cols, rows)]


def lab_table_flag_in_value(heading: str, tests: list[LabTest], *,
                            panel: str | None = None) -> list:  # fmt: skip
    """Parameter | Result | Units | Normal Range, the flag after the value."""
    tests = [replace(t, panel=panel or heading) for t in tests]
    rows = [
        Row(
            [
                t.name,
                f"{t.value} {t.flag}" if t.flag else t.value,
                t.unit or "",
                "; ".join(r.text for r in t.ranges),
            ],
            [t],
        )  # fmt: skip
        for t in tests
    ]
    return [
        Heading(heading),
        Table(["Parameter", "Result", "Units", "Normal Range"], rows),
    ]


def medicine_text(m: Medicine) -> str:
    name = m.cell() + (f" ({m.route})" if m.route else "")
    parts = [name, m.dose, m.frequency, m.duration, m.instructions]
    return " — ".join(p for p in parts if p)


def rx_lines(medicines: list[Medicine]) -> list[Line]:
    """Medicines as numbered lines, no table."""
    return [Line(f"{n}. {medicine_text(m)}", [m])
            for n, m in enumerate(medicines, start=1)]  # fmt: skip


def rx_table_strength(medicines: list[Medicine]) -> Table:
    """Drug | Strength | Frequency | Duration | Remarks: the strength in its own column."""
    rows = [
        Row(
            [
                " ".join(p for p in (m.form, m.name) if p),
                m.strength or "",
                m.frequency or "",
                m.duration or "",
                m.instructions or "",
            ],
            [m],
        )  # fmt: skip
        for m in medicines
    ]
    return Table(["Drug", "Strength", "Frequency", "Duration", "Remarks"], rows)
