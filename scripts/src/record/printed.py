"""
What the model reads off a document: the output of the classify and parse
steps.

Everything here is text exactly as printed, each item with its citation.
Nothing is derived: numbers, dates, flags from ranges, unit conversions and
dose schedules are computed by code afterwards (document.py), never by the
model (decisions 10-12).

Conventions, for the small models that fill these in (via Ollama's
JSON-schema `format`):
- Every field is required; one that isn't printed is null (or an empty
  list). Stating null explicitly is easier to check than a missing key.
- Each item starts with its citation, so the model quotes the line before
  it copies values out of it.
"""

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from record.common import Citation, PrintedText, Strict, Verbatim


class DocumentType(str, Enum):
    LAB_REPORT = "lab_report"
    PRESCRIPTION = "prescription"
    OTHER = "other"
    # Not supported in the MVP: classified as other until they are.
    DISCHARGE_SUMMARY = "discharge_summary"
    IMAGING_REPORT = "imaging_report"


# The fixed label list the classifier picks from (decision 18).
CLASSIFIER_LABELS = (
    DocumentType.LAB_REPORT,
    DocumentType.PRESCRIPTION,
    DocumentType.OTHER,
)


class ClassifierOutput(Strict):
    document_type: Literal["lab_report", "prescription", "other"]


# -------------------------------------------------------------------
# Header details
# -------------------------------------------------------------------
class PrintedPatientDetails(Strict):
    """The patient as printed on the document (used by the patient-match check)."""

    name: PrintedText | None
    age: PrintedText | None
    sex: PrintedText | None
    date_of_birth: PrintedText | None
    patient_id: PrintedText | None = Field(
        description="Patient ID, UHID, MRN or lab ID, as printed."
    )


class PrintedDocumentDetails(Strict):
    facility: PrintedText | None = Field(
        description="Laboratory, hospital or clinic name."
    )
    clinicians: list[PrintedText] = Field(
        description="Each doctor named, with their label, e.g. 'Ref. by: Dr. ...'."
    )
    report_date: PrintedText | None = Field(
        description="When the report was generated or reported."
    )
    collection_date: PrintedText | None = Field(
        description="When the sample was collected."
    )
    prescription_date: PrintedText | None


# -------------------------------------------------------------------
# Clinical items
# -------------------------------------------------------------------
class PrintedLabResult(Strict):
    """One row of a lab report."""

    citation: Citation
    panel: Verbatim | None = Field(
        description="The section heading the test is under, e.g. 'COMPLETE BLOOD COUNT'."
    )
    test_name: Verbatim
    value_text: Verbatim = Field(description="The result exactly as printed.")
    unit: Verbatim | None
    reference_ranges: list[Verbatim] = Field(
        description="Every printed reference range or interval, each exactly as printed."
    )
    flag_printed: Verbatim | None = Field(
        description="A flag printed next to the result, e.g. H, L, * or CRITICAL."
    )


class RemarkKind(str, Enum):
    ADVICE = "advice"
    COMMENT = "comment"


class RemarkScope(str, Enum):
    TEST = "test"
    PANEL = "panel"
    DOCUMENT = "document"


class PrintedRemark(Strict):
    """
    A remark, note or piece of advice. It belongs to a test only when it is
    printed on that test's row or directly under it; otherwise to the panel
    or the whole document (decision 25).
    """

    citation: Citation
    text: Verbatim
    kind: RemarkKind
    scope: RemarkScope
    test_name: Verbatim | None = Field(
        description="When scope is test: the test's name exactly as printed."
    )
    panel: Verbatim | None = Field(
        description="When scope is panel (or the test is in one): the panel heading."
    )

    @model_validator(mode="after")
    def _target_matches_scope(self):
        if self.scope is RemarkScope.TEST and self.test_name is None:
            raise ValueError("a remark on a test needs test_name")
        if self.scope is not RemarkScope.TEST and self.test_name is not None:
            raise ValueError("test_name is only set when scope is test")
        if self.scope is RemarkScope.PANEL and self.panel is None:
            raise ValueError("a remark on a panel needs panel")
        if self.scope is RemarkScope.DOCUMENT and self.panel is not None:
            raise ValueError("a remark on the document has no panel")
        return self


class ConditionKind(str, Enum):
    DIAGNOSIS = "diagnosis"
    PROVISIONAL_DIAGNOSIS = "provisional_diagnosis"
    IMPRESSION = "impression"
    HISTORY = "history"  # e.g. "K/C/O DM, HTN"


class PrintedCondition(Strict):
    """A diagnosis, impression or history exactly as printed. Never inferred."""

    citation: Citation
    text: Verbatim
    kind: ConditionKind


class PrintedMedicine(Strict):
    """One prescribed medicine, exactly as written."""

    citation: Citation
    form: Verbatim | None = Field(description="e.g. Tab, Cap, Syp, Inj.")
    name: Verbatim = Field(description="The name as written; brand names stay brands.")
    strength: Verbatim | None = Field(description="e.g. 500 mg.")
    dose: Verbatim | None = Field(description="Amount per dose, e.g. 1 tab, 5 ml.")
    frequency: Verbatim | None = Field(description="e.g. 1-0-1, BD, TDS, SOS.")
    route: Verbatim | None
    duration: Verbatim | None = Field(description="e.g. x 5 days.")
    instructions: Verbatim | None = Field(description="e.g. after food.")


class PrintedVital(Strict):
    """BP, pulse, temperature, SpO2, weight, height, BMI and the like."""

    citation: Citation
    name: Verbatim
    value_text: Verbatim
    unit: Verbatim | None


class PrintedAllergy(Strict):
    """An allergy statement, including a printed 'NKDA' or 'No known allergies'."""

    citation: Citation
    text: Verbatim
    substance: Verbatim | None
    reaction: Verbatim | None


# Placeholders so later document types fit without a redesign. They are
# part of the stored record but never of a parse schema in the MVP.
class PrintedProcedure(Strict):
    citation: Citation
    name: Verbatim
    date: PrintedText | None


class PrintedEncounter(Strict):
    citation: Citation
    kind: Verbatim = Field(description="e.g. admission, OPD visit.")
    date: PrintedText | None
    facility: PrintedText | None


class PrintedImagingFinding(Strict):
    citation: Citation
    modality: Verbatim | None
    body_site: Verbatim | None
    finding: Verbatim
    impression: Verbatim | None


# -------------------------------------------------------------------
# Parse-step output, one schema per supported document type
# -------------------------------------------------------------------
class LabReportParse(Strict):
    patient: PrintedPatientDetails
    details: PrintedDocumentDetails
    results: list[PrintedLabResult]
    remarks: list[PrintedRemark]
    conditions: list[PrintedCondition]
    vitals: list[PrintedVital]
    allergies: list[PrintedAllergy]


class PrescriptionParse(Strict):
    patient: PrintedPatientDetails
    details: PrintedDocumentDetails
    medicines: list[PrintedMedicine]
    conditions: list[PrintedCondition]
    remarks: list[PrintedRemark]
    vitals: list[PrintedVital]
    allergies: list[PrintedAllergy]


# Documents of type other are not parsed; they go to needs_review.
PARSE_MODELS = {
    DocumentType.LAB_REPORT: LabReportParse,
    DocumentType.PRESCRIPTION: PrescriptionParse,
}
