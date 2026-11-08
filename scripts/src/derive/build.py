"""
Parse output -> stored record. Every derived value is computed here, by
code, from the printed text the parse step returned; the printed values
are copied unchanged.
"""

import re
import unicodedata

from derive.dates import document_dates
from derive.medicines import parse_duration, parse_frequency
from derive.numbers import compute_flag, parse_number, parse_range
from derive.units import convert, lookup_analyte
from record.document import (
    Allergy,
    AllergyDerived,
    DocumentRecord,
    MedicationDerived,
    MedicationRequest,
    Observation,
    ObservationDerived,
    PageTranscription,
    PatientDetails,
    Remark,
    RemarkDerived,
    SourceDocument,
    StatedCondition,
    Vital,
    VitalDerived,
    VitalKind,
)
from record.printed import (
    DocumentType,
    LabReportParse,
    PrescriptionParse,
    PrintedAllergy,
    PrintedDocumentDetails,
    PrintedLabResult,
    PrintedMedicine,
    PrintedRemark,
    PrintedVital,
    RemarkScope,
)


def build_record(
    *,
    document_id: str,
    doc_type: DocumentType,
    pages: list[PageTranscription],
    has_text_layer: bool,
    parse: LabReportParse | PrescriptionParse | None,
    uploaded_by: str | None = None,
) -> DocumentRecord:
    """
    The record of one document. `parse` is None for a type that isn't
    parsed (other): the record then holds the pages and nothing else.
    Item ids are numbered per group in the order the parse lists them.
    """
    if parse is None:
        details = PrintedDocumentDetails(
            facility=None,
            clinicians=[],
            report_date=None,
            collection_date=None,
            prescription_date=None,
        )
    else:
        details = parse.details
    document = SourceDocument(
        document_id=document_id,
        type=doc_type,
        page_count=len(pages),
        has_text_layer=has_text_layer,
        uploaded_by=uploaded_by,
        printed=details,
        derived=document_dates(details),
    )
    if parse is None:
        return DocumentRecord(document=document, pages=pages)

    observations = [
        observation(f"obs-{n}", r)
        for n, r in enumerate(getattr(parse, "results", []), start=1)
    ]
    medications = [
        medication(f"med-{n}", m)
        for n, m in enumerate(getattr(parse, "medicines", []), start=1)
    ]
    return DocumentRecord(
        document=document,
        pages=pages,
        patient=PatientDetails(printed=parse.patient),
        observations=observations,
        medications=medications,
        conditions=[
            StatedCondition(id=f"cond-{n}", printed=c)
            for n, c in enumerate(parse.conditions, start=1)
        ],
        remarks=[
            remark(f"rem-{n}", r, observations)
            for n, r in enumerate(parse.remarks, start=1)
        ],
        vitals=[vital(f"vit-{n}", v) for n, v in enumerate(parse.vitals, start=1)],
        allergies=[
            allergy(f"alg-{n}", a) for n, a in enumerate(parse.allergies, start=1)
        ],
    )


# -------------------------------------------------------------------
# Items
# -------------------------------------------------------------------
def observation(item_id: str, printed: PrintedLabResult) -> Observation:
    ranges = [parse_range(text) for text in printed.reference_ranges]
    value = parse_number(printed.value_text)
    analyte = lookup_analyte(printed.test_name)
    return Observation(
        id=item_id,
        printed=printed,
        derived=ObservationDerived(
            analyte=analyte.key if analyte else None,
            value_num=value,
            ranges=[r.reference() for r in ranges],
            flag_computed=compute_flag(value, ranges, printed.unit),
            converted=convert(value, printed.unit, analyte),
        ),
    )


def medication(item_id: str, printed: PrintedMedicine) -> MedicationRequest:
    return MedicationRequest(
        id=item_id,
        printed=printed,
        derived=MedicationDerived(
            schedule=parse_frequency(printed.frequency),
            duration_days=parse_duration(printed.duration),
        ),
    )


def same_text(a: str | None, b: str | None) -> bool:
    """Equal after NFKC and collapsing whitespace."""
    if a is None or b is None:
        return a is b
    return _squash(a) == _squash(b)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def remark(
    item_id: str, printed: PrintedRemark, observations: list[Observation]
) -> Remark:
    """
    A remark on a test is linked to the one observation with that test name
    (and panel, when both name one). No unique match, no link; validator 5
    then reports the remark.
    """
    target = None
    if printed.scope is RemarkScope.TEST:
        named = [o for o in observations
                 if same_text(o.printed.test_name, printed.test_name)]  # fmt: skip
        in_panel = [o for o in named if same_text(o.printed.panel, printed.panel)]
        for candidates in (in_panel, named):
            if len(candidates) == 1:
                target = candidates[0].id
                break
            if candidates:
                break
    return Remark(
        id=item_id, printed=printed, derived=RemarkDerived(observation_id=target)
    )


_VITAL_NAMES = {
    VitalKind.BLOOD_PRESSURE: ("bp", "blood pressure"),
    VitalKind.PULSE: ("pulse", "pr", "pulse rate", "hr", "heart rate"),
    VitalKind.TEMPERATURE: ("temp", "temperature"),
    VitalKind.SPO2: ("spo2", "sp o2", "o2 saturation", "oxygen saturation"),
    VitalKind.RESPIRATORY_RATE: ("rr", "respiratory rate", "resp rate"),
    VitalKind.WEIGHT: ("weight", "wt", "body weight"),
    VitalKind.HEIGHT: ("height", "ht"),
    VitalKind.BMI: ("bmi", "body mass index"),
}
_VITAL_BY_NAME = {name: kind for kind, names in _VITAL_NAMES.items() for name in names}
_BP = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")


def _words(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w]+", " ", text).strip()


def vital(item_id: str, printed: PrintedVital) -> Vital:
    kind = _VITAL_BY_NAME.get(_words(printed.name))
    derived = VitalDerived(kind=kind)
    if kind is VitalKind.BLOOD_PRESSURE:
        if m := _BP.fullmatch(printed.value_text.strip()):
            derived = VitalDerived(
                kind=kind, systolic=float(m[1]), diastolic=float(m[2])
            )
    else:
        derived = VitalDerived(kind=kind, value_num=parse_number(printed.value_text))
    return Vital(id=item_id, printed=printed, derived=derived)


NO_KNOWN_ALLERGIES = {
    "nkda", "nka", "nil", "none", "no allergies", "no known allergies",
    "no known allergy", "no known drug allergies", "no known drug allergy",
}  # fmt: skip


def allergy(item_id: str, printed: PrintedAllergy) -> Allergy:
    return Allergy(
        id=item_id,
        printed=printed,
        derived=AllergyDerived(
            # Dots dropped first: "N.K.D.A." is NKDA.
            no_known_allergies=_words(printed.text.replace(".", ""))
            in NO_KNOWN_ALLERGIES
        ),
    )
