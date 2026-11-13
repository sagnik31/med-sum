"""
Redaction recall per identifier type, measured on the synthetic dataset:

    python -m evals.redaction

Every identifier the dataset knows (each patient's name as printed and as
registered, their hospital ID and the laboratory IDs on their reports,
their date of birth as printed), plus made-up phone, Aadhaar, ABHA, PAN
and e-mail values, is put into a sentence shaped like a stored error or
finding. That sentence is then masked twice: with the run's known
identifiers, as tracing does, and with patterns alone, as the log
formatter does. Recall is the share of identifiers that no longer appear.
See docs/adr/0008-patient-data-protection.md.
"""

import sys
from collections import defaultdict

from dataset.content import DOCUMENTS, PATIENTS
from evals.keys import answer_key
from evals.stats import wilson
from observability.redact import redact

TEMPLATES = (
    "the printed patient {} does not match the expected one",
    "Patient: {} | Page 1 of 2",
    "ValueError: could not read '{}' on line 3",
)
# Made-up values with each pattern's shape (none is a real person's).
PATTERNED = {
    "phone": ["9876543210", "+91 98765 43210", "07012345678"],
    "aadhaar": ["2345 6789 0123", "234567890123"],
    "abha": ["91-2345-6789-0123"],
    "pan": ["ABCDE1234F"],
    "email": ["meera.iyer@example.test"],
}


def identifiers() -> dict[str, list[tuple[str, list[str]]]]:
    """Each type's identifiers, with the values the run would know beside each."""
    out: dict[str, list] = defaultdict(list)
    for p in PATIENTS:
        known = [p.name, p.mrn, p.dob.isoformat()]
        out["name"].append((p.name, known))
        out["hospital_id"].append((p.mrn, known))
    for doc_id in (d.id for d in DOCUMENTS):
        patient = answer_key(doc_id).patient
        printed = patient.printed if patient else None
        if printed is None:
            continue
        known = [
            getattr(getattr(printed, f), "text", None)
            for f in ("name", "patient_id", "date_of_birth")
        ]
        for field, kind in (
            ("name", "printed_name"),
            ("patient_id", "printed_id"),
            ("date_of_birth", "date_of_birth"),
        ):
            value = getattr(printed, field)
            if value is not None:
                out[kind].append((value.text, known))
    for kind, values in PATTERNED.items():
        out[kind].extend((v, []) for v in values)
    return dict(out)


def measure() -> dict[str, dict]:
    """Per type: identifiers masked with the known values, and by patterns alone."""
    result = {}
    for kind, cases in identifiers().items():
        total = len(cases) * len(TEMPLATES)
        known = patterns = 0
        for value, seen in cases:
            for t in TEMPLATES:
                text = t.format(value)
                known += value not in redact(text, seen)
                patterns += value not in redact(text)
        result[kind] = {"total": total, "with_known": known, "patterns_only": patterns}
    return result


def report(result: dict) -> str:
    lines = [
        "| Identifier | n | Recall, known values | Recall, patterns only |",
        "|---|---|---|---|",
    ]
    for kind, r in result.items():
        cells = []
        for key in ("with_known", "patterns_only"):
            ci = wilson(r[key], r["total"])
            cells.append(f"{r[key] / r['total']:.0%} ({ci.low:.0%}-{ci.high:.0%})")
        lines.append(f"| {kind} | {r['total']} | {cells[0]} | {cells[1]} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(measure()))
    sys.exit(0)
