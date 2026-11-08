"""
What a validator reports. Findings are stored per run (section 8.4), so
they hold patient content and stay in the database: never log them.
"""

from enum import Enum

from record.common import Strict


class Validator(str, Enum):
    """The checks of section 8.3, numbered as there."""

    SOURCE_TEXT = "source_text"  # 1: each value is on its cited line
    TEXT_LAYER = "text_layer"  # 2: each value is in the PDF's own text
    UNITS_RANGES = "units_ranges"  # 3: units and ranges are consistent
    FLAG_AGREEMENT = "flag_agreement"  # 4: printed flag vs computed flag
    REMARK_PLACEMENT = "remark_placement"  # 5: a remark sits by what it's on
    MEDICINE_COVERAGE = "medicine_coverage"  # 6: no medicine invented or dropped
    DATES = "dates"  # 7: dates are real and in order
    COVERAGE = "coverage"  # 8: transcription lines no item uses
    PATIENT_MATCH = "patient_match"  # 9: printed patient vs uploaded-to patient


class Severity(str, Enum):
    """
    How the pipeline (step 8) acts on a finding:
    - error: the parse output may be wrong, so a repair (re-parse with the
      findings in the prompt) can fix it;
    - flag: the document itself is in doubt (its printed flag disagrees,
      its text layer reads differently, it names another patient), which
      a re-parse can't change: the field goes to a clinician;
    - info: shown in review, no action.
    """

    ERROR = "error"
    FLAG = "flag"
    INFO = "info"


class Finding(Strict):
    validator: Validator
    severity: Severity
    message: str
    item_id: str | None = None  # an item id, "patient" or "document"
    field: str | None = None  # e.g. value_text, reference_ranges[1]
    page: int | None = None
    lines: list[int] = []
    details: dict[str, str] = {}
