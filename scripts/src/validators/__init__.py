"""
The validators of section 8.3: deterministic checks of a built record,
never the model. Each returns findings (findings.py) for the pipeline to
store per run and route on: errors go to repair, flags to a clinician,
info to the review screen.

- source.py: 1 values on their cited lines, 2 values in the PDF text layer
- values.py: 3 units and ranges, 4 printed vs computed flag, 7 dates
- layout.py: 5 remark placement, 6 medicine coverage, 8 unused lines
- patient.py: 9 printed patient vs the patient uploaded to
"""

from datetime import date

from record.document import DocumentRecord
from record.printed import PARSE_MODELS
from validators.findings import Finding, Severity, Validator
from validators.layout import (
    check_coverage,
    check_medicine_coverage,
    check_remark_placement,
)
from validators.patient import ExpectedPatient, check_patient_match
from validators.source import check_source_text, check_text_layer
from validators.values import check_dates, check_flag_agreement, check_units_ranges

__all__ = [
    "ExpectedPatient",
    "Finding",
    "Severity",
    "Validator",
    "validate",
]


def validate(
    record: DocumentRecord,
    *,
    expected_patient: ExpectedPatient | None = None,
    today: date | None = None,
) -> list[Finding]:
    """
    Every validator, in the order of section 8.3. A document of a type
    that isn't parsed (other) has nothing to check.
    """
    if record.document.type not in PARSE_MODELS:
        return []
    return [
        *check_source_text(record),
        *check_text_layer(record),
        *check_units_ranges(record),
        *check_flag_agreement(record),
        *check_remark_placement(record),
        *check_medicine_coverage(record),
        *check_dates(record, today),
        *check_coverage(record),
        *check_patient_match(record, expected_patient),
    ]
