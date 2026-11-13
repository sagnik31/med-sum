"""
What makes an item or a document low confidence: the one list of signals,
each with an id, what kind it is, and the reason a reviewer reads.

    routing       deterministic; any hit sends the document to needs_review
    info          shown in review, never routes
    operational   how the run went (fallback model, repairs); logged and
                  stored, never routes
    model_judged  planned (a judge score, two models disagreeing); recorded
                  only until evals have calibrated them, never routes

A hit's level follows what it is on: an item id is a field-level hit on
that item, "patient" one on the printed patient details, and "document"
or no item one on the document as a whole.

Reasons are stored on an item's provenance as "LC-nn: <reason>" (with the
field after the id when there is one, "LC-nn [value_text]: ..."), so they
stay plain strings in the record schema and the id can be read back.

Errors from the validators reach this list only after repair and the
fallback model: by the time the graph collects signals, an error finding
is one that every parse attempt left in place.
"""

from dataclasses import dataclass
from enum import Enum

from pydantic import Field

from record.common import ItemStatus, Provenance, Strict
from record.document import DateSource, DocumentRecord
from record.printed import PARSE_MODELS, DocumentType
from validators import Finding, Severity, Validator
from validators.fields import item_citations
from validators.text import cited_lines

UNREADABLE = "[unreadable]"


class Kind(str, Enum):
    ROUTING = "routing"
    INFO = "info"
    OPERATIONAL = "operational"
    MODEL_JUDGED = "model_judged"


@dataclass(frozen=True)
class Signal:
    id: str
    kind: Kind
    reason: str  # what the reviewer reads

    @property
    def routes(self) -> bool:
        return self.kind is Kind.ROUTING


def _signals(*rows: tuple[str, Kind, str]) -> dict[str, Signal]:
    return {id: Signal(id, kind, reason) for id, kind, reason in rows}


R, INFO, OP, MJ = Kind.ROUTING, Kind.INFO, Kind.OPERATIONAL, Kind.MODEL_JUDGED
SIGNALS = _signals(
    ("LC-01", R, "The page marks this text as unreadable."),
    ("LC-02", R, "A page was not fully transcribed; content may be missing."),
    ("LC-03", R, "The value differs from the PDF's own text."),
    ("LC-04", R, "The value is not on the line it cites."),
    ("LC-05", R, "This medicine is not on a line that holds a medicine."),
    ("LC-06", R, "A medicine line has no entry in the record."),
    ("LC-07", R, "The remark is not printed by the test or panel it is on."),
    ("LC-08", R, "The document could not be parsed into a record."),
    ("LC-09", R, "Units or reference ranges are inconsistent."),
    ("LC-10", R, "The printed flag differs from the flag computed from the range."),
    ("LC-11", R, "A date is impossible, or out of order."),
    ("LC-12", R, "The document is undated."),
    ("LC-13", R, "The printed patient does not match the patient uploaded to."),
    ("LC-14", R, "Document type not supported; not parsed."),
    ("LC-15", R, "The model's document type and the keyword check disagree."),
    ("LC-16", R, "The value may have been read from its own reference range."),
    ("LC-17", R, "A row of a results table has no result in the record."),
    ("LC-18", R, "A model's input may have been cut short; content may be missing."),
    ("LC-I1", INFO, "No PDF text layer, so the reading has no independent check."),
    ("LC-I2", INFO, "Transcription lines that no extracted item uses."),
    ("LC-I3", INFO, "A range, flag or unit that code could not read."),
    ("OP-1", OP, "The fallback model produced the record."),
    ("OP-2", OP, "The parse was repaired before it passed."),
    ("OP-3", OP, "A model was switched off or back on by the circuit breaker."),
    ("MJ-1", MJ, "A judge model's score for the extraction (not calibrated)."),
    ("MJ-2", MJ, "The two models read the value differently (not calibrated)."),
)

# The signal of each kind of finding. Medicine coverage depends on whether
# the finding is on an item (a medicine with no medicine line) or not (a
# medicine line with no entry); see finding_signal.
_BY_FINDING = {
    (Validator.SOURCE_TEXT, Severity.ERROR): "LC-04",
    (Validator.TEXT_LAYER, Severity.FLAG): "LC-03",
    (Validator.UNITS_RANGES, Severity.FLAG): "LC-09",
    (Validator.UNITS_RANGES, Severity.INFO): "LC-I3",
    (Validator.RANGE_BOUND, Severity.FLAG): "LC-16",
    (Validator.FLAG_AGREEMENT, Severity.FLAG): "LC-10",
    (Validator.FLAG_AGREEMENT, Severity.INFO): "LC-I3",
    (Validator.REMARK_PLACEMENT, Severity.ERROR): "LC-07",
    (Validator.RESULT_COVERAGE, Severity.ERROR): "LC-17",
    (Validator.DATES, Severity.FLAG): "LC-11",
    (Validator.COVERAGE, Severity.INFO): "LC-I2",
    (Validator.PATIENT_MATCH, Severity.FLAG): "LC-13",
}

# Hits on the document as a whole, whatever item the finding names.
_DOCUMENT_LEVEL = {"LC-06", "LC-13", "LC-17"}


class SignalHit(Strict):
    """One signal raised for one item, field or document in a run."""

    signal: str
    item_id: str | None = None  # an item id, "patient", or None for the document
    field: str | None = None
    finding: int | None = Field(
        default=None, description="Index of the finding behind it, if any."
    )
    details: dict[str, int | str] = {}

    @property
    def routes(self) -> bool:
        return SIGNALS[self.signal].routes

    @property
    def reason(self) -> str:
        where = f" [{self.field}]" if self.field and self.item_id else ""
        return f"{self.signal}{where}: {SIGNALS[self.signal].reason}"


def finding_signal(finding: Finding) -> str | None:
    """The signal a finding raises, or None for one that raises none."""
    if finding.validator is Validator.MEDICINE_COVERAGE:
        if finding.severity is not Severity.ERROR:
            return None
        return "LC-05" if finding.item_id else "LC-06"
    return _BY_FINDING.get((finding.validator, finding.severity))


def from_findings(findings: list[Finding]) -> list[SignalHit]:
    hits = []
    for n, f in enumerate(findings):
        signal = finding_signal(f)
        if signal is None:
            continue
        item = (
            None if signal in _DOCUMENT_LEVEL or f.item_id == "document" else f.item_id
        )
        hits.append(SignalHit(signal=signal, item_id=item, field=f.field, finding=n))
    return hits


def unreadable(record: DocumentRecord) -> list[SignalHit]:
    """LC-01: an item, or a header value, cites a line marked unreadable."""
    hits = []
    for item_id, field, citation in item_citations(record):
        lines = cited_lines(record, citation) or []
        if any(UNREADABLE in line for line in lines):
            item = None if item_id == "document" else item_id
            hits.append(SignalHit(signal="LC-01", item_id=item, field=field))
    return hits


def collect(
    *,
    doc_type: DocumentType | None,
    record: DocumentRecord | None,
    parsed: bool,
    findings: list[Finding],
    capped_pages: list[int] = (),
    truncated_pages: list[int] = (),
    parse_truncated: bool = False,
    classify_disagree: bool = False,
    record_model_role: str | None = None,
    repairs: int = 0,
    breaker_switches: list[dict] = (),
) -> list[SignalHit]:
    """
    Every signal for a document at the end of its run, in a fixed order.
    `parsed` says whether the record was built from a parse (False for
    type other, and for a parsed type whose every parse attempt failed).
    `breaker_switches` are the run's circuit breaker switches, one OP-3 hit
    each ({event, model, model_role}, as observability's Run notes them).
    `truncated_pages` and `parse_truncated` say which calls' prompts may
    have been cut to fit the context window (LC-18: a page's transcription
    request, or a part of the parse the record was built from).
    """
    hits: list[SignalHit] = []
    if record is not None:
        hits += unreadable(record)
    hits += [SignalHit(signal="LC-02", details={"page": page}) for page in capped_pages]
    hits += [
        SignalHit(signal="LC-18", details={"page": page}) for page in truncated_pages
    ]
    if parse_truncated:
        hits.append(SignalHit(signal="LC-18", details={"step": "parse"}))
    hits += from_findings(findings)
    if doc_type in PARSE_MODELS and not parsed:
        hits.append(SignalHit(signal="LC-08"))
    if (
        parsed
        and record is not None
        and record.document.derived.date_source is DateSource.UNDATED
    ):
        hits.append(SignalHit(signal="LC-12"))
    if doc_type is DocumentType.OTHER:
        hits.append(SignalHit(signal="LC-14"))
    if classify_disagree:
        hits.append(SignalHit(signal="LC-15"))
    if record is not None and not record.document.has_text_layer:
        hits.append(SignalHit(signal="LC-I1"))
    if parsed and record_model_role == "fallback":
        hits.append(SignalHit(signal="OP-1"))
    if repairs:
        hits.append(SignalHit(signal="OP-2", details={"repairs": repairs}))
    hits += [SignalHit(signal="OP-3", details=dict(s)) for s in breaker_switches]
    return hits


def needs_review(hits: list[SignalHit]) -> bool:
    return any(h.routes for h in hits)


def apply(record: DocumentRecord, hits: list[SignalHit]) -> None:
    """
    Mark the record's items from the routing hits: an item with a hit is
    low confidence with its reasons, every other item validated. Hits on
    the document (or on an item id the record doesn't have) go on the
    document's provenance. Every holder must already have a provenance
    (observability.stamp). Info, operational and model-judged hits don't
    change the record; they are stored with the run.
    """
    holders: dict[str | None, Provenance] = {None: record.document.provenance}
    if record.patient is not None:
        holders["patient"] = record.patient.provenance
    for item in record.items():
        holders[item.id] = item.provenance
    if any(p is None for p in holders.values()):
        raise ValueError("stamp the record's provenance before applying signals")

    reasons: dict[str | None, list[str]] = {}
    for h in hits:
        if not h.routes:
            continue
        key = h.item_id if h.item_id in holders else None
        reasons.setdefault(key, [])
        if h.reason not in reasons[key]:
            reasons[key].append(h.reason)
    for key, provenance in holders.items():
        if key in reasons:
            provenance.low_confidence_reasons = reasons[key]
            provenance.status = ItemStatus.LOW_CONFIDENCE
        else:
            provenance.low_confidence_reasons = []
            provenance.status = ItemStatus.VALIDATED
