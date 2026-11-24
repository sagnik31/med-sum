"""
Printed dates, and which one is the document's date: a prescription's own
date; otherwise the report date, else the collection date. With none, the
document is undated. See docs/adr/0003-printed-and-derived-values.md.

Dates are read day first, as Indian documents print them: 05/09/2024 is
5 September. A two-digit year is not read, since its century would be a
guess. A time after the date is allowed and ignored.
"""

import re
from datetime import date

from record.document import DateSource, DocumentDates
from record.printed import DocumentType, PrintedDocumentDetails

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}  # fmt: skip

_TIME = r"(?:\s*,?\s*\d{1,2}[:.]\d{2}(?::\d{2})?(?:\s*[AaPp]\.?\s*[Mm]\.?)?)?"
_NUMERIC = re.compile(rf"(\d{{1,2}})\s*([/.-])\s*(\d{{1,2}})\s*\2\s*(\d{{4}}){_TIME}")
_NAMED = re.compile(
    rf"(\d{{1,2}})(?:st|nd|rd|th)?[\s-]*([A-Za-z]+)\.?[\s,-]*(\d{{4}}){_TIME}"
)
_ISO = re.compile(rf"(\d{{4}})-(\d{{2}})-(\d{{2}}){_TIME}")


def parse_date(text: str | None) -> date | None:
    """The date a printed text states, or None when it isn't a real date."""
    if text is None:
        return None
    text = text.strip()
    try:
        if m := _NUMERIC.fullmatch(text):
            return date(int(m[4]), int(m[3]), int(m[1]))
        if m := _NAMED.fullmatch(text):
            month = MONTHS.get(m[2].lower())
            return date(int(m[3]), month, int(m[1])) if month else None
        if m := _ISO.fullmatch(text):
            return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:  # 31/02/2025 and the like
        return None
    return None


# The order the document date is picked in, and the order for a prescription:
# a report date on one is another document's ("Prior report: 11/04/2025").
DATE_RULES = (
    DateSource.REPORT_DATE,
    DateSource.COLLECTION_DATE,
    DateSource.PRESCRIPTION_DATE,
)


PRESCRIPTION_RULES = (DateSource.PRESCRIPTION_DATE, *DATE_RULES[:2])


def document_dates(
    details: PrintedDocumentDetails,
    entered: dict[str, str] | None = None,
    *,
    doc_type: DocumentType | None = None,
) -> DocumentDates:
    """
    Every printed date, and the document date: the report date, otherwise
    the collection date, otherwise the prescription date; else undated. A
    prescription's own date comes first; one dated by another date only is
    flagged (validator 7).
    A printed date that can't be read is skipped (validator 7 reports it).
    `entered` holds dates a clinician added in review where none was
    printed (field -> text); they count as that field's date, by the same
    rule. See docs/adr/0003-printed-and-derived-values.md.
    """
    entered = entered or {}
    rules = DATE_RULES
    if doc_type is DocumentType.PRESCRIPTION:
        rules = PRESCRIPTION_RULES
    found = {}
    for rule in DATE_RULES:
        printed = getattr(details, rule.value)
        text = printed.text if printed else entered.get(rule.value)
        found[rule] = parse_date(text) if text else None
    chosen = next((rule for rule in rules if found[rule] is not None), None)
    return DocumentDates(
        report_date=found[DateSource.REPORT_DATE],
        collection_date=found[DateSource.COLLECTION_DATE],
        prescription_date=found[DateSource.PRESCRIPTION_DATE],
        document_date=found[chosen] if chosen else None,
        date_source=chosen or DateSource.UNDATED,
    )
