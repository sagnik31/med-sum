"""
Clinician review of a stored record: confirming an item's reading or
correcting one of its fields. See docs/adr/0011-clinician-review.md.

Rules:
- The model's reading is never overwritten. A correction is appended to
  the item's `corrections` (the field, the model's reading, the new value,
  who and when); `printed` stays exactly as the model read it.
- `reviewed_view` is the record as a clinician has left it: each corrected
  field replaced by its latest correction and the item's derived values
  computed again from it, by the same code that built them. The patient
  summary and the document page show this view; the stored record keeps
  both readings.
- A corrected value goes through the checks that apply to a value on its
  own (units and ranges, printed vs computed flag, dates), never the
  checks against the transcription: the clinician read the source
  document, not the transcription. What they find is returned for the
  clinician to see; it does not refuse the value (the document itself may
  be wrong, and is never "corrected" by code).
- Any item can be reviewed, flagged or not. A confirmation or correction
  sets the item's status, `reviewed_by` and `reviewed_at`; its
  low-confidence reasons stay, so it is known afterwards what the review
  answered. There is no second approver.
- A document is verified when nothing in it is low confidence any more.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from derive.build import allergy, medication, observation, remark, vital
from derive.dates import document_dates, parse_date
from record.common import (
    REVIEWED,
    Correction,
    ItemStatus,
    PrintedText,
    Provenance,
    Strict,
)
from record.document import DocumentRecord
from validators.findings import Finding
from validators.values import check_dates, check_flag_agreement, check_units_ranges

# Ids that name the record's two header holders; every other id is a
# clinical item's.
PATIENT = "patient"
DOCUMENT = "document"

# Fields that are not readings and can't be corrected: where it is printed,
# and the labels the model chose from a fixed list.
NOT_CORRECTABLE = {"citation", "kind", "scope"}

# The document's dates: when none was read, a clinician may add one, so an
# undated document can be dated in review. The date is kept as a correction
# with no original; the view derives the document date from it.
# See docs/adr/0003-printed-and-derived-values.md.
DATE_FIELDS = ("report_date", "collection_date", "prescription_date")

MAX_VALUE_CHARS = 500
MAX_NOTE_CHARS = 1000

# "reference_ranges[1]" -> ("reference_ranges", 1)
_PATH = re.compile(r"^([a-z_]+)(?:\[(\d+)\])?$")
_SIGNAL = re.compile(r"^(LC-(?:I)?\d+)\b")


class ReviewError(ValueError):
    """The review can't be applied. The message names ids and fields only."""


class ReviewAction(str, Enum):
    CONFIRM = "confirm"  # the reading is right as it is
    CORRECT = "correct"  # one field's right value is entered


class ReviewRequest(Strict):
    action: ReviewAction
    item_id: str  # an item id, "patient" or "document"
    field: str | None = None  # for a correction: e.g. value_text, clinicians[0]
    value: str | None = None  # for a correction: the right value
    note: str | None = None
    reviewer: str  # the clinician's name, as the summary shows it
    at: datetime


@dataclass(frozen=True)
class ReviewResult:
    record: DocumentRecord  # the stored record with the review added
    findings: list[Finding]  # the checks on a corrected value, for this item
    signals: list[str]  # the item's low-confidence signal ids before review
    original: str | None  # the model's reading of the corrected field
    flagged: int  # holders still low confidence after this review


# -------------------------------------------------------------------
# Holders: the patient, the document and each clinical item
# -------------------------------------------------------------------
def _holders(record: DocumentRecord):
    """(id, holder) for everything that has a provenance."""
    yield DOCUMENT, record.document
    if record.patient is not None:
        yield PATIENT, record.patient
    for item in record.items():
        yield item.id, item


def _holder(record: DocumentRecord, item_id: str):
    for hid, holder in _holders(record):
        if hid == item_id:
            return holder
    raise ReviewError(f"the record has no item {item_id!r}")


def flagged(record: DocumentRecord) -> int:
    """How many holders are still low confidence (awaiting review)."""
    return sum(
        1
        for _, h in _holders(record)
        if h.provenance is not None and h.provenance.status is ItemStatus.LOW_CONFIDENCE
    )


def signal_ids(reasons: list[str]) -> list[str]:
    """The distinct signal ids ("LC-04", ...) of a list of reasons, in order."""
    out = []
    for reason in reasons:
        m = _SIGNAL.match(reason)
        if m and m[1] not in out:
            out.append(m[1])
    return out


# -------------------------------------------------------------------
# Reading and writing one field of a printed value
# -------------------------------------------------------------------
def _split(path: str) -> tuple[str, int | None]:
    m = _PATH.match(path or "")
    if not m:
        raise ReviewError(f"{path!r} is not a field")
    return m[1], (int(m[2]) if m[2] is not None else None)


def _annotation_allows_printed_text(model, name: str) -> bool:
    info = type(model).model_fields.get(name)
    return info is not None and "PrintedText" in str(info.annotation)


def read_field(printed, path: str) -> str | None:
    """
    The text of one field of a printed value: a plain text field, a
    PrintedText's text, or an entry of a list of either. Raises ReviewError
    for a field that isn't a reading or doesn't exist.
    """
    name, index = _split(path)
    if name in NOT_CORRECTABLE or name not in type(printed).model_fields:
        raise ReviewError(f"{path!r} can't be corrected")
    value = getattr(printed, name)
    if index is not None:
        if not isinstance(value, list) or index >= len(value):
            raise ReviewError(f"{path!r} is not on this item")
        value = value[index]
    elif isinstance(value, list):
        raise ReviewError(f"{path!r} is a list: name one entry, e.g. {name}[0]")
    if isinstance(value, PrintedText):
        return value.text
    if value is None:
        if _annotation_allows_printed_text(printed, name):
            # Nothing was printed, so there is no place it was read from.
            raise ReviewError(
                f"nothing was read for {path!r}, so it can't be corrected"
            )
        return None
    if isinstance(value, Enum) or not isinstance(value, str):
        raise ReviewError(f"{path!r} can't be corrected")
    return value


def _write_field(printed, path: str, text: str):
    """A copy of the printed value with one field's text replaced."""
    name, index = _split(path)
    value = getattr(printed, name)

    def replaced(old):
        if isinstance(old, PrintedText):
            return old.model_copy(update={"text": text})
        return text

    if index is not None:
        value = [replaced(v) if i == index else v for i, v in enumerate(value)]
    else:
        value = replaced(value)
    return printed.model_copy(update={name: value})


# -------------------------------------------------------------------
# The record as reviewed
# -------------------------------------------------------------------
def _corrected(printed, corrections: list[Correction]):
    for c in corrections:  # in order, so the latest of a field wins
        if getattr(printed, c.field, None) is None and c.field in DATE_FIELDS:
            continue  # an added date: nothing printed to replace (_entered)
        printed = _write_field(printed, c.field, c.corrected)
    return printed


def _entered(printed, corrections: list[Correction]) -> dict[str, str]:
    """The dates a clinician added where none was printed (latest wins)."""
    return {
        c.field: c.corrected
        for c in corrections
        if c.field in DATE_FIELDS and getattr(printed, c.field, None) is None
    }


def reviewed_view(record: DocumentRecord) -> DocumentRecord:
    """
    The record with every correction applied and the derived values of
    each corrected item computed again. A record without corrections is
    returned as it is.
    """
    holders = [h for _, h in _holders(record)]
    if not any(h.corrections for h in holders if hasattr(h, "corrections")):
        return record
    keep = ("provenance", "corrections")

    def rebuilt(item, build):
        fresh = build(item.id, _corrected(item.printed, item.corrections))
        return fresh.model_copy(update={k: getattr(item, k) for k in keep})

    def plain(item):
        if not item.corrections:
            return item
        return item.model_copy(
            update={"printed": _corrected(item.printed, item.corrections)}
        )

    def each(items, build):
        return [rebuilt(i, build) if i.corrections else i for i in items]

    document = record.document
    if document.corrections:
        details = _corrected(document.printed, document.corrections)
        added = _entered(document.printed, document.corrections)
        document = document.model_copy(
            update={"printed": details, "derived": document_dates(details, added)}
        )
    patient = record.patient
    if patient is not None and patient.corrections:
        patient = patient.model_copy(
            update={"printed": _corrected(patient.printed, patient.corrections)}
        )
    observations = each(record.observations, observation)
    # A remark's link to its test is computed again only if the remark or
    # a test's name was corrected; otherwise it stays as it was built.
    relink = any(r.corrections for r in record.remarks) or any(
        o.corrections for o in record.observations
    )
    remarks = record.remarks
    if relink:
        remarks = [
            remark(r.id, _corrected(r.printed, r.corrections), observations)
            .model_copy(update={k: getattr(r, k) for k in keep})
            for r in record.remarks
        ]  # fmt: skip
    view = record.model_copy(
        update={
            "document": document,
            "patient": patient,
            "observations": observations,
            "medications": each(record.medications, medication),
            "conditions": [plain(c) for c in record.conditions],
            "remarks": remarks,
            "vitals": each(record.vitals, vital),
            "allergies": each(record.allergies, allergy),
            "procedures": [plain(p) for p in record.procedures],
            "encounters": [plain(e) for e in record.encounters],
            "imaging_findings": [plain(f) for f in record.imaging_findings],
        }
    )
    # Validated again as a whole: a correction may not break the record's
    # own rules (unique ids, remark links, the chosen date).
    return DocumentRecord.model_validate(view.model_dump(mode="json"))


def latest_correction(holder, field: str | None = None) -> Correction | None:
    """The holder's latest correction (of one field, when given)."""
    for c in reversed(getattr(holder, "corrections", [])):
        if field is None or c.field == field:
            return c
    return None


# -------------------------------------------------------------------
# Applying a review
# -------------------------------------------------------------------
def _value_checks(view: DocumentRecord, item_id: str, today: date | None):
    """The checks that apply to a value on its own, for one holder."""
    findings = [
        *check_units_ranges(view),
        *check_flag_agreement(view),
        *check_dates(view, today),
    ]
    return [f for f in findings if f.item_id == item_id]


def apply_review(
    record: DocumentRecord, request: ReviewRequest, *, today: date | None = None
) -> ReviewResult:
    holder = _holder(record, request.item_id)
    provenance: Provenance | None = holder.provenance
    if provenance is None:
        raise ReviewError(f"{request.item_id!r} has no provenance to review")
    note = (request.note or "").strip() or None
    if note is not None and len(note) > MAX_NOTE_CHARS:
        raise ReviewError(f"the note is longer than {MAX_NOTE_CHARS} characters")
    signals = signal_ids(provenance.low_confidence_reasons)
    update: dict = {}
    original = None

    if request.action is ReviewAction.CONFIRM:
        if request.field is not None or request.value is not None:
            raise ReviewError("a confirmation is of the whole item: no field or value")
        # A corrected item stays corrected: confirming it again confirms
        # the corrected values, and the corrections stand.
        status = (
            provenance.status
            if provenance.status is ItemStatus.CLINICIAN_CORRECTED
            else ItemStatus.CLINICIAN_CONFIRMED
        )
    else:
        if request.field is None:
            raise ReviewError("a correction names the field it corrects")
        if not hasattr(holder, "corrections"):
            raise ReviewError(f"{request.item_id!r} can't be corrected")
        value = (request.value or "").strip()
        if not value:
            raise ReviewError("a correction needs the right value")
        if len(value) > MAX_VALUE_CHARS:
            raise ReviewError(f"the value is longer than {MAX_VALUE_CHARS} characters")
        if (
            request.item_id == DOCUMENT
            and request.field in DATE_FIELDS
            and getattr(holder.printed, request.field) is None
        ):
            # No date was read here: on an undated document the clinician
            # adds one from the source; a dated one has its printed dates.
            if any(getattr(holder.printed, f) is not None for f in DATE_FIELDS):
                raise ReviewError(
                    f"nothing was read for {request.field!r} and the document "
                    "has a printed date: correct that one"
                )
            original = None
            on = parse_date(value)
            if on is None:
                raise ReviewError(
                    f"{request.field!r} is not a date that can be read (day "
                    "first, four-digit year)"
                )
            if on > (today or date.today()):
                raise ReviewError(f"{request.field!r} is in the future")
        else:
            original = read_field(holder.printed, request.field)
        current = latest_correction(holder, request.field)
        shown = current.corrected if current is not None else original
        if value == shown:
            raise ReviewError("the value is unchanged: confirm the item instead")
        correction = Correction(
            field=request.field,
            original=original,
            corrected=value,
            clinician=request.reviewer,
            at=request.at,
            note=note,
        )
        update["corrections"] = [*holder.corrections, correction]
        status = ItemStatus.CLINICIAN_CORRECTED

    update["provenance"] = provenance.model_copy(
        update={
            "status": status,
            "reviewed_by": request.reviewer,
            "reviewed_at": request.at,
        }
    )
    new_holder = holder.model_copy(update=update)
    new_record = _replace(record, request.item_id, new_holder)

    findings = []
    if request.action is ReviewAction.CORRECT:
        findings = _value_checks(reviewed_view(new_record), request.item_id, today)
    return ReviewResult(new_record, findings, signals, original, flagged(new_record))


def _replace(record: DocumentRecord, item_id: str, holder) -> DocumentRecord:
    if item_id == DOCUMENT:
        update = {"document": holder}
    elif item_id == PATIENT:
        update = {"patient": holder}
    else:
        update = {}
        for group in type(record).model_fields:
            items = getattr(record, group)
            if isinstance(items, list) and any(
                getattr(i, "id", None) == item_id for i in items
            ):
                update[group] = [holder if i.id == item_id else i for i in items]
                break
    new = record.model_copy(update=update)
    return DocumentRecord.model_validate(new.model_dump(mode="json"))


def is_reviewed(provenance: Provenance | None) -> bool:
    return provenance is not None and provenance.status in REVIEWED
