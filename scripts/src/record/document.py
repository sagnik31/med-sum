"""
The stored record of one document (section 8.1 of the plan): a JSONB value
with a schema version.

Each clinical item keeps three things apart:
- `printed`: what the model read, verbatim, with its citation (printed.py);
- `derived`: what code computed from it (numbers, dates, flags, schedules);
- `provenance` and `corrections`: what produced it, and any clinician edits.

The names follow FHIR resources so a later mapping is easy, but this is not
FHIR and carries no codes (decision 14).

The validators here only enforce what code is responsible for (unique IDs,
remark links, page numbering, the chosen date). Whether the model's
readings are right is checked by the pipeline's validators, whose failures
are stored per run rather than refused here.
"""

from datetime import date
from enum import Enum
from typing import Iterator, Literal

from pydantic import Field, model_validator

from record.common import SCHEMA_VERSION, Correction, Provenance, Strict, Verbatim
from record.printed import (
    DocumentType,
    PrintedAllergy,
    PrintedCondition,
    PrintedDocumentDetails,
    PrintedEncounter,
    PrintedImagingFinding,
    PrintedLabResult,
    PrintedMedicine,
    PrintedPatientDetails,
    PrintedProcedure,
    PrintedRemark,
    PrintedVital,
    RemarkScope,
)


# -------------------------------------------------------------------
# Derived values (computed by code, never by the model)
# -------------------------------------------------------------------
class ComputedFlag(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class ReferenceRange(Strict):
    """One range parsed from a printed range text; a text can hold several."""

    text: Verbatim = Field(description="The printed range it was parsed from.")
    low: float | None = None
    high: float | None = None
    label: str | None = Field(
        default=None, description="Who it applies to when printed, e.g. Male, Adult."
    )


class ConvertedValue(Strict):
    """A value converted from a fixed table in code; the printed value stays."""

    value: float
    unit: str
    from_unit: str


class ObservationDerived(Strict):
    analyte: str | None = Field(
        default=None, description="Key in the unit table; same test across documents."
    )
    value_num: float | None = Field(
        default=None, description="Only when the printed value parses without guessing."
    )
    ranges: list[ReferenceRange] = []
    flag_computed: ComputedFlag | None = Field(
        default=None, description="Only when exactly one range clearly applies."
    )
    converted: ConvertedValue | None = None


class DoseSchedule(Strict):
    """A parsed frequency: 1-0-1 is morning 1, noon 0, night 1; BD is twice a day."""

    morning: float | None = None
    noon: float | None = None
    night: float | None = None
    times_per_day: float | None = None
    as_needed: bool = False


class MedicationDerived(Strict):
    schedule: DoseSchedule | None = None
    duration_days: int | None = None


class VitalKind(str, Enum):
    BLOOD_PRESSURE = "blood_pressure"
    PULSE = "pulse"
    TEMPERATURE = "temperature"
    SPO2 = "spo2"
    RESPIRATORY_RATE = "respiratory_rate"
    WEIGHT = "weight"
    HEIGHT = "height"
    BMI = "bmi"


class VitalDerived(Strict):
    kind: VitalKind | None = None
    value_num: float | None = None
    systolic: float | None = None
    diastolic: float | None = None


class AllergyDerived(Strict):
    no_known_allergies: bool = False


class RemarkDerived(Strict):
    observation_id: str | None = Field(
        default=None, description="The observation a test remark belongs to."
    )


# -------------------------------------------------------------------
# Record items
# -------------------------------------------------------------------
class RecordItem(Strict):
    id: Verbatim
    # Optional so answer keys can leave it out; the pipeline always sets it.
    provenance: Provenance | None = None
    corrections: list[Correction] = []


class Observation(RecordItem):
    printed: PrintedLabResult
    derived: ObservationDerived = Field(default_factory=ObservationDerived)


class MedicationRequest(RecordItem):
    printed: PrintedMedicine
    derived: MedicationDerived = Field(default_factory=MedicationDerived)


class StatedCondition(RecordItem):
    printed: PrintedCondition


class Remark(RecordItem):
    printed: PrintedRemark
    derived: RemarkDerived = Field(default_factory=RemarkDerived)


class Vital(RecordItem):
    printed: PrintedVital
    derived: VitalDerived = Field(default_factory=VitalDerived)


class Allergy(RecordItem):
    printed: PrintedAllergy
    derived: AllergyDerived = Field(default_factory=AllergyDerived)


class Procedure(RecordItem):
    printed: PrintedProcedure


class Encounter(RecordItem):
    printed: PrintedEncounter


class ImagingFinding(RecordItem):
    printed: PrintedImagingFinding


class PatientDetails(Strict):
    printed: PrintedPatientDetails
    provenance: Provenance | None = None
    corrections: list[Correction] = []


# -------------------------------------------------------------------
# The document and its pages
# -------------------------------------------------------------------
class DateSource(str, Enum):
    """Which rule chose the document date (decision 13)."""

    REPORT_DATE = "report_date"
    COLLECTION_DATE = "collection_date"
    PRESCRIPTION_DATE = "prescription_date"
    UNDATED = "undated"


class DocumentDates(Strict):
    report_date: date | None = None
    collection_date: date | None = None
    prescription_date: date | None = None
    document_date: date | None = None
    date_source: DateSource = DateSource.UNDATED

    @model_validator(mode="after")
    def _document_date_follows_its_source(self):
        if self.date_source is DateSource.UNDATED:
            if self.document_date is not None:
                raise ValueError("an undated document has no document_date")
        elif self.document_date != getattr(self, self.date_source.value):
            raise ValueError(
                f"document_date must equal {self.date_source.value}, its source"
            )
        return self


class SourceDocument(Strict):
    document_id: str
    type: DocumentType
    page_count: int = Field(ge=1)
    has_text_layer: bool
    uploaded_by: str | None = None
    printed: PrintedDocumentDetails
    derived: DocumentDates = Field(default_factory=DocumentDates)
    provenance: Provenance | None = None


class PageTranscription(Strict):
    """
    One page as transcribed by the vision model, split into lines. Line n
    is lines[n - 1]; every citation points into these lines.
    """

    page: int = Field(ge=1)
    lines: list[str]
    text_layer: str | None = Field(
        default=None, description="The PDF's own text for this page, when it has one."
    )

    def numbered_text(self) -> str:
        """The page as the parse step sees it: each line prefixed with its number."""
        return "\n".join(f"{n}| {line}" for n, line in enumerate(self.lines, start=1))

    def quote(self, first_line: int, last_line: int) -> str:
        """The text of lines first_line..last_line (1-based, inclusive)."""
        if not 1 <= first_line <= last_line <= len(self.lines):
            raise IndexError(
                f"lines {first_line}-{last_line} are not on page {self.page} "
                f"({len(self.lines)} lines)"
            )
        return "\n".join(self.lines[first_line - 1 : last_line])


def numbered_document(pages: list[PageTranscription]) -> str:
    """Every page's numbered text, under a page heading."""
    return "\n\n".join(f"=== Page {p.page} ===\n{p.numbered_text()}" for p in pages)


# -------------------------------------------------------------------
# The record
# -------------------------------------------------------------------
class DocumentRecord(Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    document: SourceDocument
    pages: list[PageTranscription]
    patient: PatientDetails | None = None
    observations: list[Observation] = []
    medications: list[MedicationRequest] = []
    conditions: list[StatedCondition] = []
    remarks: list[Remark] = []
    vitals: list[Vital] = []
    allergies: list[Allergy] = []
    procedures: list[Procedure] = []
    encounters: list[Encounter] = []
    imaging_findings: list[ImagingFinding] = []

    def items(self) -> Iterator[RecordItem]:
        """Every clinical item, in a fixed order."""
        for group in (
            self.observations,
            self.medications,
            self.conditions,
            self.remarks,
            self.vitals,
            self.allergies,
            self.procedures,
            self.encounters,
            self.imaging_findings,
        ):
            yield from group

    def remarks_for(self, observation_id: str) -> list[Remark]:
        return [r for r in self.remarks if r.derived.observation_id == observation_id]

    @model_validator(mode="after")
    def _pages_match_the_document(self):
        numbers = [p.page for p in self.pages]
        if numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError(f"pages must be numbered 1..n in order, got {numbers}")
        if len(self.pages) != self.document.page_count:
            raise ValueError(
                f"document has {self.document.page_count} pages "
                f"but {len(self.pages)} transcriptions"
            )
        return self

    @model_validator(mode="after")
    def _item_ids_are_unique(self):
        seen = set()
        for item in self.items():
            if item.id in seen:
                raise ValueError(f"duplicate item id {item.id!r}")
            seen.add(item.id)
        return self

    @model_validator(mode="after")
    def _remarks_point_at_observations(self):
        observations = {o.id for o in self.observations}
        for remark in self.remarks:
            target = remark.derived.observation_id
            if target is None:
                continue
            if remark.printed.scope is not RemarkScope.TEST:
                raise ValueError(
                    f"remark {remark.id!r} is on the {remark.printed.scope.value} "
                    "but points at an observation"
                )
            if target not in observations:
                raise ValueError(
                    f"remark {remark.id!r} points at unknown observation {target!r}"
                )
        return self
