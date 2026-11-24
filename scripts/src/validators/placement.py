"""
Validators 11 and 12: each printed part in its own field, and each result
read from a row of results, not from a table's header.

The first live evals found these in `qwen3.5:4b`'s records (and the
fallback's), each passing every other check, since every value was on its
cited line:

- 11 field placement: a part of the item printed in the wrong field.
  - a medicine's strength left in its name ("Telma 40 mg", no strength);
  - a schedule in the dose field ("1-0-0" as the dose, no frequency): one
    the derived schedule then never reads;
  - a printed flag left in the value ("11.2 L", no flag): the value is no
    longer a number, so no flag is computed or compared;
  - a unit left out: the row's unit cell prints one and the result has none.
    Without it the value is compared with nothing in another unit;
  - an allergy whose text leaves out its own substance or reaction (text
    "Sulfa drugs", reaction "rash"): the text is the whole statement, so
    its parts are in it.
- 12 header row: a result read from a results table's header or separator
  row ("Test | Result | Flag ...", "--- | ---") rather than a data row.

Each is an error, so a re-parse with the finding can fix it; one that
survives repair and the fallback is LC-23 or LC-24. A field is never
corrected by code: the model's parse is what was read, and moving a part
would make code the author of a field nobody read.
See docs/adr/0004-deterministic-checks-decide-routing.md and
docs/adr/0013-model-choice-after-the-first-live-evals.md.
"""

import re
import unicodedata

from derive.medicines import parse_frequency
from record.document import DocumentRecord
from validators.findings import Finding, Severity, Validator
from validators.layout import is_table_furniture, unit_cells
from validators.text import cited_numbers, normalise

# A strength at the end of a name: a number and a strength unit ("40 mg",
# "40/5 mg", "60,000 IU", "60K", "1%"). A number with no unit ("Glycomet
# GP 1") is part of a brand name.
_STRENGTH_AT_END = re.compile(
    r"\s(?P<strength>\d[\d,./]*\s*(?:mg|mcg|µg|μg|ug|gm|g|ml|iu|units?|k|%))$",
    re.IGNORECASE,
)
# A printed flag left at the end of a value, after a space ("11.2 L").
# Only flags with a direction or "*": a letter such as "A" or "N" could be
# part of a value.
_FLAG_AT_END = re.compile(
    r"^(?P<value>\S*\d\S*)\s+(?P<flag>hh|ll|h|l|high|low|↑|↓|\*)\*?$",
    re.IGNORECASE,
)


# The most lines a finding names: a citation may claim any last line.
MAX_LINES = 50


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _finding(validator, message, item, field, **details) -> Finding:
    c = item.printed.citation
    return Finding(
        validator=validator,
        severity=Severity.ERROR,
        message=message,
        item_id=item.id,
        field=field,
        page=c.page,
        lines=list(range(c.first_line, min(c.last_line, c.first_line + MAX_LINES) + 1)),
        details=details,
    )


def check_field_placement(record: DocumentRecord) -> list[Finding]:
    findings = []
    v = Validator.FIELD_PLACEMENT
    units = {p.page: unit_cells(p.lines) for p in record.pages}
    for m in record.medications:
        p = m.printed
        strength = _STRENGTH_AT_END.search(p.name)
        if strength and not p.strength:
            findings.append(_finding(
                v, f"medicine {p.name!r} has its strength "
                f"{strength['strength']!r} in its name and no strength: the "
                "strength goes in its own field", m, "name",
                strength=strength["strength"]))  # fmt: skip
        if p.dose and parse_frequency(p.dose) is not None:
            findings.append(_finding(
                v, f"the dose of medicine {p.name!r} is {p.dose!r}, which is a "
                "frequency: a dose is the amount per dose, and the frequency "
                "goes in its own field", m, "dose"))  # fmt: skip
    for o in record.observations:
        p = o.printed
        flag = _FLAG_AT_END.match(p.value_text)
        # "1.5 L" with no unit field is a volume in litres, not a flag.
        litres = flag and flag["flag"].casefold() == "l" and not p.unit
        if flag and p.flag_printed is None and not litres:
            findings.append(_finding(
                v, f"result {p.test_name!r} has the flag {flag['flag']!r} in its "
                f"value {p.value_text!r} and no printed flag: the flag goes in "
                "its own field", o, "value_text", flag=flag["flag"]))  # fmt: skip
        page = p.citation.page
        printed = {
            unit
            for n in cited_numbers(record, p.citation)
            if (unit := units.get(page, {}).get(n))
        }
        # Not one printed in the value as well ("10.4 g/dL").
        if len(printed) == 1 and not p.unit:
            (unit,) = printed
            if normalise(unit) not in normalise(p.value_text):
                findings.append(_finding(
                    v, f"result {p.test_name!r} has no unit, and its row prints "
                    f"{unit!r} in the unit column", o, "unit"))  # fmt: skip
    for a in record.allergies:
        p = a.printed
        text = _norm(p.text)
        for name in ("substance", "reaction"):
            part = getattr(p, name)
            if part and _norm(part) not in text:
                findings.append(_finding(
                    v, f"the allergy's text {p.text!r} leaves out its {name} "
                    f"{part!r}: the text is the whole printed statement", a,
                    "text", part=name))  # fmt: skip
    return findings


def check_header_rows(record: DocumentRecord) -> list[Finding]:
    """A result cited only on a table's header or separator row."""
    findings = []
    for o in record.observations:
        c = o.printed.citation
        if not 1 <= c.page <= len(record.pages):
            continue
        lines = record.pages[c.page - 1].lines
        cited = range(c.first_line, c.last_line + 1)
        if c.last_line <= len(lines) and all(
            is_table_furniture(lines, n) for n in cited
        ):
            findings.append(_finding(
                Validator.HEADER_ROW, f"result {o.printed.test_name!r} is read "
                "from a table's header or separator row, not from a row of "
                "results", o, "citation"))  # fmt: skip
    return findings
