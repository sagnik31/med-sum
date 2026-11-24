"""
Validator 9: the patient printed on the
document is the patient it was uploaded to. Each detail that is known on
both sides is compared; a mismatch flags the document for a clinician.

Until patients are records of their own, a patient is a login with only a
full name, so the caller
passes whatever it knows; details it doesn't know are not compared.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from derive.dates import parse_date
from record.common import PrintedText
from record.document import DocumentRecord
from validators.findings import Finding, Severity, Validator
from validators.text import cited_lines, normalise


@dataclass(frozen=True)
class ExpectedPatient:
    name: str | None = None
    sex: str | None = None  # M, F, or as entered
    date_of_birth: date | None = None
    age_years: int | None = None  # when only an age is known
    mrn: str | None = None  # the hospital's patient ID


HONORIFICS = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "master", "baby", "smt", "shri",
    "sri", "kum", "kumari",
}  # fmt: skip


def name_tokens(name: str) -> list[str]:
    text = unicodedata.normalize("NFKC", name).casefold()
    words = re.sub(r"[^\w]+", " ", text).split()
    return [w for w in words if w not in HONORIFICS]


def names_match(printed: str, expected: str) -> bool:
    """
    Same words in any order, or in order with initials for some ("M. Iyer").
    A printed name of initials only ("A R") never matches: it fits too many
    patients to confirm one.
    """
    a, b = name_tokens(printed), name_tokens(expected)
    if not a or not b:
        return False
    if all(len(x) == 1 for x in a):
        return False
    if sorted(a) == sorted(b):
        return True
    if len(a) != len(b):
        return False
    return all(
        x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
        for x, y in zip(a, b)
    )


_SEXES = {"m": "M", "male": "M", "f": "F", "female": "F", "o": "O", "other": "O"}
_AGE = re.compile(r"(\d{1,3})\s*(?:y|yr|yrs|year|years)?\.?", re.IGNORECASE)
# A printed ID is the hospital's own only under these labels; a lab's ID isn't.
# The label is read on the cited lines: a quote may be the ID alone.
_HOSPITAL_ID = re.compile(r"\b(?:uhid|mrn|hospital\s*(?:id|no))\b", re.IGNORECASE)


_OTHER_ID = re.compile(
    r"\b(?:lab|accession|sample|specimen|barcode|bill|invoice|visit|ref|"
    r"registration|reg)\.?\s*(?:id|no|number|#)\b",
    re.IGNORECASE,
)


def _other_hospital_id(record: DocumentRecord, pid: PrintedText, mrn: str) -> bool:
    """
    Whether the hospital ID printed for `pid` is another than `mrn`. The
    label nearest before the ID says whose it is. Under another label (a
    lab's number) the hospital's own ID is elsewhere on the lines, so the
    MRN must be somewhere on them.
    """
    lines = cited_lines(record, pid.citation)
    text = normalise("\n".join(lines) if lines else pid.citation.quote)
    if not _HOSPITAL_ID.search(text):
        return False
    at = text.find(normalise(pid.text))
    before = text[:at] if at >= 0 else ""
    own = [m.end() for m in _HOSPITAL_ID.finditer(before)]
    other = [m.end() for m in _OTHER_ID.finditer(before)]
    if other and (not own or other[-1] > own[-1]):
        return _id_key(mrn) not in _id_key(text)
    return _id_key(pid.text) != _id_key(mrn)


def read_sex(text: str) -> str | None:
    return _SEXES.get(text.strip().rstrip(".").casefold())


def read_age(text: str) -> int | None:
    """Years from "57 Y", "45 Years", "57"; None for months or days ("6 M")."""
    m = _AGE.fullmatch(text.strip())
    return int(m[1]) if m else None


def age_on(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


# The message of the finding for a document on which nothing that names
# the patient was read (signal LC-22, pipeline.signals).
UNCONFIRMED = "no printed name, patient ID or date of birth was read"


def _known(expected: ExpectedPatient) -> bool:
    return bool(expected.name or expected.mrn or expected.date_of_birth)


def check_patient_match(
    record: DocumentRecord, expected: ExpectedPatient | None
) -> list[Finding]:
    """
    Validator 9: the printed patient against the patient the document was
    uploaded to. A mismatch is a flag on the field. When the patient is
    known but nothing that names them (name, patient ID, date of birth) was
    read, the document can't be tied to them: a flag on the document with
    no field, so it goes to review instead of ending checks_passed. See
    docs/adr/0004-deterministic-checks-decide-routing.md.
    """
    if expected is None:
        return []
    printed = record.patient.printed if record.patient is not None else None
    if printed is None or not (
        printed.name or printed.patient_id or printed.date_of_birth
    ):
        if not _known(expected):
            return []
        return [Finding(validator=Validator.PATIENT_MATCH, severity=Severity.FLAG,
                        message=UNCONFIRMED, item_id="document")]  # fmt: skip
    findings = []

    def flag(field: str, value: PrintedText, message: str):
        findings.append(Finding(
            validator=Validator.PATIENT_MATCH, severity=Severity.FLAG, message=message,
            item_id="patient", field=field, page=value.citation.page,
            lines=[value.citation.first_line]))  # fmt: skip

    if (
        printed.name
        and expected.name
        and not names_match(printed.name.text, expected.name)
    ):
        flag("name", printed.name,
             f"printed for {printed.name.text!r}, uploaded to {expected.name!r}")  # fmt: skip

    if printed.sex and expected.sex:
        a, b = read_sex(printed.sex.text), read_sex(expected.sex)
        if a and b and a != b:
            flag("sex", printed.sex, f"printed sex {printed.sex.text!r}, patient's is "
                 f"{expected.sex!r}")  # fmt: skip

    on = record.document.derived.document_date
    if printed.age:
        age = read_age(printed.age.text)
        want = expected.age_years
        if expected.date_of_birth and on:
            want = age_on(expected.date_of_birth, on)
        if age is not None and want is not None and abs(age - want) > 1:
            flag("age", printed.age, f"printed age {printed.age.text!r}, the patient "
                 f"was {want} on the document's date")  # fmt: skip

    if printed.date_of_birth and expected.date_of_birth:
        born = parse_date(printed.date_of_birth.text)
        if born is not None and born != expected.date_of_birth:
            flag("date_of_birth", printed.date_of_birth,
                 f"printed date of birth {printed.date_of_birth.text!r} is not the "
                 "patient's")  # fmt: skip

    pid = printed.patient_id
    if pid and expected.mrn and _other_hospital_id(record, pid, expected.mrn):
        flag("patient_id", pid, f"printed hospital ID {pid.text!r} is not the "
             f"patient's {expected.mrn!r}")  # fmt: skip
    return findings


def _id_key(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()
