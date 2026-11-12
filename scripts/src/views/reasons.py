"""
Low-confidence reasons in plain words. A stored reason is
"LC-nn: <reason>" or "LC-nn [field]: <reason>" (pipeline/signals.py); a
reader sees the signal's reason text from the signal table and, for a
field, which value it is about, without the id.
"""

import re
from dataclasses import dataclass

from pipeline.signals import SIGNALS

_REASON = re.compile(r"^(?P<id>[A-Z]{2}-[A-Z0-9]+)(?: \[(?P<field>.*?)\])?: ")

# What a reader calls each field a reason can name.
FIELD_LABELS = {
    "value_text": "value",
    "unit": "unit",
    "reference_ranges": "reference range",
    "flag_printed": "printed flag",
    "test_name": "test name",
    "panel": "panel",
    "name": "name",
    "text": "text",
    "strength": "strength",
    "dose": "dose",
    "frequency": "frequency",
    "duration": "duration",
    "route": "route",
    "instructions": "instructions",
    "form": "form",
    "substance": "substance",
    "reaction": "reaction",
    "age": "age",
    "sex": "sex",
    "date_of_birth": "date of birth",
    "patient_id": "patient ID",
    "facility": "facility",
    "clinicians": "clinician",
    "report_date": "report date",
    "collection_date": "collection date",
    "prescription_date": "prescription date",
    "scope": "what it is attached to",
    "citation": "cited line",
}


@dataclass(frozen=True)
class PlainReason:
    text: str  # the signal's reason, as the signal table words it
    field: str | None = None  # the value it is about, in words


def plain(reason: str) -> PlainReason:
    """A stored reason in plain words. Text that isn't a known signal stays as it is."""
    m = _REASON.match(reason)
    if m is None or m["id"] not in SIGNALS:
        return PlainReason(reason)
    field = m["field"]
    if field is not None:
        base = field.split(".")[0].split("[")[0]
        field = FIELD_LABELS.get(base, base.replace("_", " "))
    return PlainReason(SIGNALS[m["id"]].reason, field)
