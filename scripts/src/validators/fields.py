"""
Every printed value of a record, with the citation it must be found at.
Validators 1, 2 and 8 walk this list, so they agree on what was extracted.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from record.common import Citation, PrintedText
from record.document import DocumentRecord


class Place(Enum):
    ON_LINES = "on its cited lines"
    # A panel name is the heading above its row; a remark's test name may be
    # the row above it (the synthetic dataset showed both).
    ON_OR_ABOVE = "on or above its cited lines"


@dataclass(frozen=True)
class CitedField:
    item_id: str  # an item id, "patient" or "document"
    field: str
    text: str
    citation: Citation
    place: Place = Place.ON_LINES

    @property
    def is_header(self) -> bool:
        """Header values have a citation each; an item's fields share one."""
        return self.item_id in ("patient", "document")


def cited_fields(record: DocumentRecord) -> Iterator[CitedField]:
    if record.patient is not None:
        patient = record.patient.printed
        for name in ("name", "age", "sex", "date_of_birth", "patient_id"):
            yield from _header("patient", name, getattr(patient, name))
    details = record.document.printed
    yield from _header("document", "facility", details.facility)
    for n, clinician in enumerate(details.clinicians):
        yield from _header("document", f"clinicians[{n}]", clinician)
    for name in ("report_date", "collection_date", "prescription_date"):
        yield from _header("document", name, getattr(details, name))

    for o in record.observations:
        p = o.printed
        yield from _fields(o.id, p.citation, test_name=p.test_name,
                           value_text=p.value_text, unit=p.unit,
                           flag_printed=p.flag_printed)  # fmt: skip
        for n, text in enumerate(p.reference_ranges):
            yield CitedField(o.id, f"reference_ranges[{n}]", text, p.citation)
        if p.panel is not None:
            yield CitedField(o.id, "panel", p.panel, p.citation, Place.ON_OR_ABOVE)
    for m in record.medications:
        p = m.printed
        yield from _fields(m.id, p.citation, form=p.form, name=p.name,
                           strength=p.strength, dose=p.dose, frequency=p.frequency,
                           route=p.route, duration=p.duration,
                           instructions=p.instructions)  # fmt: skip
    for c in record.conditions:
        yield from _fields(c.id, c.printed.citation, text=c.printed.text)
    for r in record.remarks:
        p = r.printed
        yield from _fields(r.id, p.citation, text=p.text)
        for name in ("test_name", "panel"):
            if (text := getattr(p, name)) is not None:
                yield CitedField(r.id, name, text, p.citation, Place.ON_OR_ABOVE)
    for v in record.vitals:
        p = v.printed
        yield from _fields(v.id, p.citation, name=p.name, value_text=p.value_text,
                           unit=p.unit)  # fmt: skip
    for a in record.allergies:
        p = a.printed
        yield from _fields(a.id, p.citation, text=p.text, substance=p.substance,
                           reaction=p.reaction)  # fmt: skip


def item_citations(
    record: DocumentRecord,
) -> Iterator[tuple[str, str | None, Citation]]:
    """(item id, header field or None, citation): each citation once."""
    seen = set()
    for f in cited_fields(record):
        key = (f.item_id, f.field if f.is_header else None)
        if key not in seen:
            seen.add(key)
            yield key[0], key[1], f.citation


def _header(item_id: str, field: str, value: PrintedText | None):
    if value is not None:
        yield CitedField(item_id, field, value.text, value.citation)


def _fields(item_id: str, citation: Citation, **values: str | None):
    for field, text in values.items():
        if text is not None:
            yield CitedField(item_id, field, text, citation)
