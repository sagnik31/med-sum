"""
Building blocks shared by what the model reads off a page and what code
stores about it: verbatim text, citations, provenance and corrections.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Version of the stored record (DocumentRecord). Bump it on any change to
# the record's shape; the exported schema files carry it in their names.
SCHEMA_VERSION = 1

Verbatim = Annotated[str, StringConstraints(min_length=1)]
"""Text exactly as printed: never empty, never corrected or normalised."""


class Strict(BaseModel):
    """
    Base of every schema model. Unknown keys are errors, so neither the
    model nor a hand-written answer key can add fields that nothing checks.
    """

    model_config = ConfigDict(extra="forbid")


class Citation(Strict):
    """Where an item is printed: lines of one page's numbered transcription."""

    page: int = Field(ge=1, description="Page number, starting at 1.")
    first_line: int = Field(ge=1, description="First line number of the item.")
    last_line: int = Field(
        ge=1, description="Last line number; the same as first_line for one line."
    )
    quote: Verbatim = Field(
        description="The text of those lines, copied exactly, without line numbers."
    )

    @model_validator(mode="after")
    def _lines_in_order(self):
        if self.last_line < self.first_line:
            raise ValueError("last_line comes before first_line")
        return self


class PrintedText(Strict):
    """One printed value, such as a name or a date, with where it is printed."""

    citation: Citation
    text: Verbatim = Field(description="The value exactly as printed.")


class ItemStatus(str, Enum):
    EXTRACTED = "extracted"  # produced by the model, not yet validated
    VALIDATED = "validated"  # passed every validator that applies
    LOW_CONFIDENCE = "low_confidence"  # flagged; waits for a clinician
    CLINICIAN_CONFIRMED = "clinician_confirmed"  # a clinician accepted the reading
    CLINICIAN_CORRECTED = "clinician_corrected"  # a clinician entered a value


REVIEWED = {ItemStatus.CLINICIAN_CONFIRMED, ItemStatus.CLINICIAN_CORRECTED}


class Provenance(Strict):
    """What produced an item, and where it stands in review."""

    run_id: str
    node: str = Field(description="Pipeline node, e.g. parse or repair.")
    step_id: str | None = Field(
        default=None, description="The pipeline step (span) that produced the item."
    )
    model: str | None = None
    prompt_name: str | None = None
    prompt_hash: str | None = None
    status: ItemStatus = ItemStatus.EXTRACTED
    low_confidence_reasons: list[str] = []
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def _status_is_consistent(self):
        if self.status is ItemStatus.LOW_CONFIDENCE and not self.low_confidence_reasons:
            raise ValueError("a low-confidence item needs its reasons")
        if self.low_confidence_reasons and self.status in (
            ItemStatus.EXTRACTED,
            ItemStatus.VALIDATED,
        ):
            raise ValueError(f"an item with reasons cannot be {self.status.value}")
        reviewed = self.reviewed_by is not None and self.reviewed_at is not None
        if (self.status in REVIEWED) != reviewed:
            raise ValueError(
                "reviewed_by and reviewed_at are set exactly when a clinician "
                "has confirmed or corrected the item"
            )
        return self


class Correction(Strict):
    """
    A value a clinician entered. It sits next to the original extraction,
    which is never overwritten.
    """

    field: str = Field(description="Field of the printed item, e.g. value_text.")
    original: str | None
    corrected: str
    clinician: str
    at: datetime
    note: str | None = None
