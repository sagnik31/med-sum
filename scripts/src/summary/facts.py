"""
The facts of a patient summary, computed by code from the patient's
document records. Nothing here is inferred: every fact is a value as
printed on a document, a value code derived from one (a flag from the
printed range, a document date), or a count or ordering of those.

Rules:
- Documents are ordered by their document date (the printed report date,
  else the collection date; a prescription's printed date). An undated
  document goes last, is labelled undated, and is never used for a trend
  or for "the latest" of anything.
- A trend is only "rising" or "falling": two or more values of the same
  test in the same printed unit, from dated documents on different dates,
  each one higher (or lower) than the one before. Anything else has no
  trend. A trend says how many reports it is based on and cites each.
- A document that awaits review is included. A value awaiting review is
  shown with its reasons, and is not used for a trend.
- No organ systems, no severities, no confidence labels, no diagnoses
  other than those printed as diagnoses.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from record.common import Citation, ItemStatus, Provenance
from record.document import (
    DocumentRecord,
    MedicationRequest,
    Observation,
    RecordItem,
    Vital,
)
from record.printed import DocumentType, PrintedText, RemarkScope
from views.reasons import PlainReason, plain

PARSED_TYPES = (DocumentType.LAB_REPORT, DocumentType.PRESCRIPTION)


@dataclass(frozen=True)
class SummaryInput:
    """One of the patient's documents, as the summary job reads it."""

    document_id: str
    name: str  # the uploaded file's name
    uploaded_at: datetime
    status: str | None  # insights.status; None when there is no job yet
    record: DocumentRecord | None


@dataclass(frozen=True)
class Source:
    """Where a fact is printed: document number (D1, D2, ... in date order), page, lines."""

    doc: int
    page: int
    first_line: int
    last_line: int


@dataclass
class DocEntry:
    number: int
    document_id: str
    name: str
    type: DocumentType
    date: date | None
    date_source: str
    status: str  # verified or needs_review
    record: DocumentRecord = field(repr=False)
    awaiting: int = 0  # items and header values awaiting review
    reasons: list[PlainReason] = field(default_factory=list)  # document-level

    @property
    def parsed(self) -> bool:
        return self.type in PARSED_TYPES

    def source(self, citation: Citation) -> Source:
        return Source(
            self.number, citation.page, citation.first_line, citation.last_line
        )


@dataclass
class Fact:
    """One item of one document, with its review state."""

    doc: DocEntry
    item: RecordItem
    source: Source
    awaiting: list[PlainReason]

    @property
    def date(self) -> date | None:
        return self.doc.date


@dataclass
class Trend:
    direction: str  # "rising" or "falling"
    unit: str | None
    values: list[Fact]  # oldest first; "based on len(values) reports"


@dataclass
class Series:
    """Every value of one test (or vital) across documents, oldest first, undated last."""

    key: str
    name: str  # as printed on the latest document that has it
    panel: str | None
    values: list[Fact]
    trend: Trend | None = None

    @property
    def latest(self) -> Fact | None:
        """The value on the latest dated document; undated values never count."""
        dated = [v for v in self.values if v.date is not None]
        return dated[-1] if dated else None


@dataclass
class Prescription:
    doc: DocEntry
    clinicians: list[PrintedText]
    medicines: list[Fact]


@dataclass
class PatientHeader:
    doc: DocEntry  # the document these details are printed on
    name: PrintedText | None
    age: PrintedText | None
    sex: PrintedText | None
    patient_id: PrintedText | None


@dataclass
class Facts:
    documents: list[DocEntry]  # every included document, in date order
    not_included: int  # documents without a finished record
    patient: PatientHeader | None
    attention: list[Fact]  # latest value of a test with a printed or computed flag
    allergies: list[Fact]
    latest_prescription: Prescription | None
    earlier_prescriptions: list[Prescription]
    conditions: list[Fact]
    labs: list[Series]
    vitals: list[Series]
    remarks: list[Fact]

    @property
    def dated(self) -> list[DocEntry]:
        return [d for d in self.documents if d.date is not None]

    @property
    def undated(self) -> list[DocEntry]:
        return [d for d in self.documents if d.date is None]

    @property
    def awaiting(self) -> int:
        return sum(d.awaiting for d in self.documents)


# -------------------------------------------------------------------
# Building the facts
# -------------------------------------------------------------------
def _awaits(p: Provenance | None) -> bool:
    return p is not None and p.status is ItemStatus.LOW_CONFIDENCE


def _reasons(p: Provenance | None) -> list[PlainReason]:
    return [plain(r) for r in p.low_confidence_reasons] if _awaits(p) else []


def order_documents(inputs: list[SummaryInput]) -> tuple[list[DocEntry], int]:
    """The documents with a finished record, in document-date order (undated last)."""
    usable = [
        i
        for i in inputs
        if i.record is not None and i.status in ("verified", "needs_review")
    ]
    usable.sort(
        key=lambda i: (
            i.record.document.derived.document_date is None,
            i.record.document.derived.document_date or date.min,
            i.uploaded_at,
        )
    )
    docs = []
    for n, i in enumerate(usable, start=1):
        r = i.record
        holders = [r.patient.provenance if r.patient else None]
        holders += [it.provenance for it in r.items()]
        docs.append(
            DocEntry(
                number=n,
                document_id=i.document_id,
                name=i.name,
                type=r.document.type,
                date=r.document.derived.document_date,
                date_source=r.document.derived.date_source.value,
                status=i.status,
                record=r,
                awaiting=sum(1 for p in holders if _awaits(p)),
                reasons=_reasons(r.document.provenance),
            )
        )
    return docs, len(inputs) - len(usable)


def _fact(doc: DocEntry, item: RecordItem) -> Fact:
    return Fact(doc, item, doc.source(item.printed.citation), _reasons(item.provenance))


def _series_key(o: Observation) -> str:
    if o.derived.analyte:
        return "analyte:" + o.derived.analyte
    return "name:" + " ".join(o.printed.test_name.casefold().split())


def _vital_key(v: Vital) -> str:
    if v.derived.kind is not None:
        return "kind:" + v.derived.kind.value
    return "name:" + " ".join(v.printed.name.casefold().split())


def _unit(fact: Fact) -> str | None:
    unit = fact.item.printed.unit
    return " ".join(unit.casefold().split()) if unit else None


def _number(fact: Fact) -> float | None:
    return fact.item.derived.value_num


def trend(values: list[Fact]) -> Trend | None:
    """
    "rising" or "falling", or None. Uses dated values with a number that
    don't await review, in the unit of the latest such value, one per
    date; needs at least two, each strictly above (or below) the last.
    """
    usable = [
        v
        for v in values
        if v.date is not None and _number(v) is not None and not v.awaiting
    ]
    if len(usable) < 2:
        return None
    unit = _unit(usable[-1])
    same = [v for v in usable if _unit(v) == unit]
    dates = [v.date for v in same]
    if len(same) < 2 or len(set(dates)) != len(dates):
        return None
    nums = [_number(v) for v in same]
    pairs = list(zip(nums, nums[1:]))
    if all(b > a for a, b in pairs):
        direction = "rising"
    elif all(b < a for a, b in pairs):
        direction = "falling"
    else:
        return None
    return Trend(direction, same[-1].item.printed.unit, same)


def _series(facts: list[Fact], key_of, name_of, panel_of) -> list[Series]:
    groups: dict[str, list[Fact]] = {}
    for f in facts:
        groups.setdefault(key_of(f.item), []).append(f)
    out = []
    for key, values in groups.items():
        last = values[-1]  # documents are in date order, undated last
        latest = next((v for v in reversed(values) if v.date is not None), last)
        s = Series(key, name_of(latest.item), panel_of(latest.item), values)
        s.trend = trend(values)
        out.append(s)
    return out


def _prescription(doc: DocEntry) -> Prescription:
    r = doc.record
    return Prescription(
        doc, list(r.document.printed.clinicians), [_fact(doc, m) for m in r.medications]
    )


def _header(docs: list[DocEntry]) -> PatientHeader | None:
    with_patient = [d for d in docs if d.record.patient is not None]
    if not with_patient:
        return None
    dated = [d for d in with_patient if d.date is not None]
    doc = (dated or with_patient)[-1]
    p = doc.record.patient.printed
    return PatientHeader(doc, p.name, p.age, p.sex, p.patient_id)


def build_facts(inputs: list[SummaryInput]) -> Facts:
    docs, not_included = order_documents(inputs)
    parsed = [d for d in docs if d.parsed]

    observations = [_fact(d, o) for d in parsed for o in d.record.observations]
    labs = _series(
        observations,
        _series_key,
        lambda o: o.printed.test_name,
        lambda o: o.printed.panel,
    )
    vitals = _series(
        [_fact(d, v) for d in parsed for v in d.record.vitals],
        _vital_key,
        lambda v: v.printed.name,
        lambda v: None,
    )

    attention = []
    for s in labs:
        latest = s.latest
        if latest is None:
            continue
        o: Observation = latest.item
        if o.printed.flag_printed is not None or (
            o.derived.flag_computed is not None
            and o.derived.flag_computed.value != "normal"
        ):
            attention.append(latest)

    prescriptions = [
        _prescription(d) for d in parsed if d.type is DocumentType.PRESCRIPTION
    ]
    dated_rx = [p for p in prescriptions if p.doc.date is not None]
    latest_rx = dated_rx[-1] if dated_rx else None
    # Earlier prescriptions: most recent first, then the undated ones.
    earlier = [p for p in reversed(dated_rx) if p is not latest_rx]
    earlier += [p for p in prescriptions if p.doc.date is None]

    return Facts(
        documents=docs,
        not_included=not_included,
        patient=_header(docs),
        attention=attention,
        allergies=[_fact(d, a) for d in parsed for a in d.record.allergies],
        latest_prescription=latest_rx,
        earlier_prescriptions=earlier,
        conditions=[_fact(d, c) for d in parsed for c in d.record.conditions],
        labs=labs,
        vitals=vitals,
        remarks=[_fact(d, r) for d in parsed for r in d.record.remarks],
    )


def remark_target(fact: Fact) -> tuple[str, str | None]:
    """What a remark is attached to, as printed: (scope, test or panel name)."""
    p = fact.item.printed
    if p.scope is RemarkScope.TEST:
        return "test", p.test_name
    if p.scope is RemarkScope.PANEL:
        return "panel", p.panel
    return "document", None


def medicine_line(m: MedicationRequest) -> list[str]:
    """A medicine's printed parts, in reading order, without the empty ones."""
    p = m.printed
    return [
        v
        for v in (
            p.form,
            p.name,
            p.strength,
            p.dose,
            p.frequency,
            p.route,
            p.duration,
            p.instructions,
        )  # fmt: skip
        if v is not None
    ]
