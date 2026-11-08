"""
Validators 3, 4 and 7: units and ranges, printed against computed flags,
and dates. They check what the document says, so what they find is a flag
for a clinician, not something a re-parse can fix.
"""

from datetime import date

from derive.dates import parse_date
from derive.numbers import has_digits, parse_range, same_unit
from derive.units import convertible, lookup_analyte
from record.document import ComputedFlag, DocumentRecord
from validators.findings import Finding, Severity, Validator


# -------------------------------------------------------------------
# 3: units and reference ranges
# -------------------------------------------------------------------
def check_units_ranges(record: DocumentRecord) -> list[Finding]:
    findings = []

    def add(severity, message, o, field):
        findings.append(Finding(
            validator=Validator.UNITS_RANGES, severity=severity, message=message,
            item_id=o.id, field=field, page=o.printed.citation.page,
            lines=[o.printed.citation.first_line]))  # fmt: skip

    for o in record.observations:
        p = o.printed
        for n, text in enumerate(p.reference_ranges):
            field = f"reference_ranges[{n}]"
            r = parse_range(text)
            if not r.parsed:
                if has_digits(text):
                    add(Severity.INFO, f"range {text!r} could not be read, so no "
                        "flag is computed from it", o, field)  # fmt: skip
                continue
            if r.low is not None and r.high is not None and r.low > r.high:
                add(Severity.FLAG, f"range {text!r} has its low bound above its high "
                    "bound", o, field)  # fmt: skip
            if (
                r.unit is not None
                and p.unit is not None
                and not same_unit(r.unit, p.unit)
            ):
                add(Severity.FLAG, f"range {text!r} is in {r.unit!r} but the value is "
                    f"in {p.unit!r}", o, field)  # fmt: skip

        analyte = lookup_analyte(p.test_name)
        if analyte is not None and p.unit is not None:
            fits = convertible(p.unit, analyte)
            if fits is None:
                add(Severity.INFO, f"unit {p.unit!r} is not in the unit table, so the "
                    "value is not converted", o, "unit")  # fmt: skip
            elif not fits:
                add(Severity.FLAG, f"unit {p.unit!r} does not fit {p.test_name} "
                    f"(expected a unit like {analyte.unit!r})", o, "unit")  # fmt: skip
    return findings


# -------------------------------------------------------------------
# 4: printed flag against computed flag (decision 11)
# -------------------------------------------------------------------
ABNORMAL = "abnormal"  # a printed flag with no direction, such as "*"
PRINTED_FLAGS = {
    "h": ComputedFlag.HIGH, "high": ComputedFlag.HIGH, "hh": ComputedFlag.HIGH,
    "↑": ComputedFlag.HIGH, "critical high": ComputedFlag.HIGH,
    "l": ComputedFlag.LOW, "low": ComputedFlag.LOW, "ll": ComputedFlag.LOW,
    "↓": ComputedFlag.LOW, "critical low": ComputedFlag.LOW,
    "n": ComputedFlag.NORMAL, "normal": ComputedFlag.NORMAL,
    "*": ABNORMAL, "a": ABNORMAL, "abnormal": ABNORMAL, "critical": ABNORMAL,
}  # fmt: skip


def read_printed_flag(text: str | None):
    """A printed flag's direction; None when nothing is printed, "" when unknown."""
    if text is None:
        return None
    key = " ".join(text.casefold().split())
    if key in PRINTED_FLAGS:
        return PRINTED_FLAGS[key]
    if len(key) > 1 and key.rstrip("*") in PRINTED_FLAGS:  # "H*"
        return PRINTED_FLAGS[key.rstrip("*")]
    return ""


def check_flag_agreement(record: DocumentRecord) -> list[Finding]:
    """
    The document is never corrected: a disagreement keeps both flags and
    goes to a clinician. Nothing is compared without a computed flag.
    """
    findings = []
    for o in record.observations:
        computed = o.derived.flag_computed
        if computed is None:
            continue
        printed = read_printed_flag(o.printed.flag_printed)
        message = None
        severity = Severity.FLAG
        if printed == "":
            severity = Severity.INFO
            message = (
                f"printed flag {o.printed.flag_printed!r} is not one this check knows"
            )
        elif printed is None:
            if computed is not ComputedFlag.NORMAL:
                message = f"the value is {computed.value} for its range, but no flag is printed"
        elif printed == ABNORMAL:
            if computed is ComputedFlag.NORMAL:
                message = (f"flag {o.printed.flag_printed!r} is printed, but the value "
                           "is within its range")  # fmt: skip
        elif printed is not computed:
            message = (f"flag {o.printed.flag_printed!r} is printed, but the value is "
                       f"{computed.value} for its range")  # fmt: skip
        if message:
            findings.append(Finding(
                validator=Validator.FLAG_AGREEMENT, severity=severity, message=message,
                item_id=o.id, field="flag_printed", page=o.printed.citation.page,
                lines=[o.printed.citation.first_line],
                details={"printed": o.printed.flag_printed or "",
                         "computed": computed.value}))  # fmt: skip
    return findings


# -------------------------------------------------------------------
# 7: dates
# -------------------------------------------------------------------
def check_dates(record: DocumentRecord, today: date | None = None) -> list[Finding]:
    """Printed dates are real, not in the future, and collection <= report."""
    today = today or date.today()
    findings = []

    def add(message, item_id, field, printed):
        findings.append(Finding(
            validator=Validator.DATES, severity=Severity.FLAG, message=message,
            item_id=item_id, field=field, page=printed.citation.page,
            lines=[printed.citation.first_line]))  # fmt: skip

    details = record.document.printed
    read = {}
    for field in ("report_date", "collection_date", "prescription_date"):
        printed = getattr(details, field)
        if printed is None:
            continue
        on = parse_date(printed.text)
        if on is None:
            add(f"{field} {printed.text!r} is not a date that can be read (day "
                "first, four-digit year)", "document", field, printed)  # fmt: skip
        elif on > today:
            add(
                f"{field} {printed.text!r} is in the future", "document", field, printed
            )
        elif on.year < 1900:
            add(f"{field} {printed.text!r} is before 1900", "document", field, printed)
        read[field] = on

    collected, reported = read.get("collection_date"), read.get("report_date")
    if collected and reported and collected > reported:
        add(f"collected on {collected.isoformat()}, after the report date "
            f"{reported.isoformat()}", "document", "collection_date",
            details.collection_date)  # fmt: skip

    patient = record.patient.printed if record.patient else None
    if patient is not None and patient.date_of_birth is not None:
        born = parse_date(patient.date_of_birth.text)
        latest = record.document.derived.document_date or today
        if born is None:
            add(f"date of birth {patient.date_of_birth.text!r} is not a date that "
                "can be read", "patient", "date_of_birth", patient.date_of_birth)  # fmt: skip
        elif born > latest:
            add(f"date of birth {born.isoformat()} is after the document's date",
                "patient", "date_of_birth", patient.date_of_birth)  # fmt: skip
    return findings
