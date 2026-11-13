"""
Masking patient identifiers in text that leaves the clinical stores:
validator findings and error text stored on telemetry rows (pipeline_steps,
pipeline_runs), job and summary errors, and log messages.

Two layers, strongest first:

1. Known values: the identifiers a run has seen (the expected patient's
   name and ID, and the name, ID and date of birth printed on the
   document). Each is removed whole, and a name's parts too, so "Mrs.
   Iyer" goes as well as "Meera Iyer".
2. Patterns for identifiers with a fixed shape: e-mail addresses, Indian
   mobile numbers, ABHA numbers, Aadhaar numbers, PAN, and hospital or
   laboratory IDs written as letters, a hyphen and digits (SH-100231,
   SD-25-092011).

Names inside free text that no run has seen are not caught: that would
take a named-entity model, and the clinical stores hold names on purpose.
The clinical stores are never redacted. See
docs/adr/0008-patient-data-protection.md.
"""

import re
from collections.abc import Iterable

# (label, pattern), applied in order: longer shapes before the shorter
# ones they contain (an ABHA number holds twelve digits in a row of four).
PATTERNS = (
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("[ABHA]", re.compile(r"(?<![\w-])\d{2}-\d{4}-\d{4}-\d{4}(?![\w-])")),
    ("[AADHAAR]", re.compile(r"(?<![\w-])\d{4}[ -]?\d{4}[ -]?\d{4}(?![\w-])")),
    (
        "[PHONE]",
        re.compile(r"(?<![\w+-])(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?![\w-])"),
    ),
    ("[PAN]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("[ID]", re.compile(r"\b[A-Z]{2,5}(?:-\d{2,4})*-\d{5,}\b")),
)
PATIENT = "[PATIENT]"
# Name parts shorter than this are left alone ("Md", initials).
MIN_NAME_PART = 3
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "master", "baby", "smt", "shri"}


def _parts(value: str) -> list[str]:
    words = re.findall(r"[^\W\d_]+", value)
    return [w for w in words if len(w) >= MIN_NAME_PART and w.lower() not in _TITLES]


def known_patterns(values: Iterable[str]) -> list[re.Pattern]:
    """Patterns for known identifiers: each whole value, longest first, then its name parts."""
    wholes, parts = set(), set()
    for value in values:
        value = (value or "").strip()
        if len(value) < MIN_NAME_PART:
            continue
        wholes.add(value)
        parts.update(_parts(value))
    out = []
    for v in sorted(wholes, key=len, reverse=True):
        spaced = r"\s+".join(re.escape(w) for w in v.split())
        out.append(re.compile(rf"(?<!\w){spaced}(?!\w)", re.IGNORECASE))
    for p in sorted(parts, key=len, reverse=True):
        out.append(re.compile(rf"(?<!\w){re.escape(p)}(?!\w)", re.IGNORECASE))
    return out


def redact(text: str | None, known: Iterable[str] = ()) -> str | None:
    """`text` with known identifiers, then patterned ones, replaced by labels."""
    if not text:
        return text
    for pattern in known_patterns(known):
        text = pattern.sub(PATIENT, text)
    for label, pattern in PATTERNS:
        text = pattern.sub(label, text)
    return text


def redact_finding(finding: dict, known: Iterable[str] = ()) -> dict:
    """A finding (as stored) with its message and detail values redacted."""
    known = list(known)
    out = dict(finding)
    out["message"] = redact(out.get("message"), known)
    details = out.get("details")
    if isinstance(details, dict):
        out["details"] = {
            k: redact(v, known) if isinstance(v, str) else v for k, v in details.items()
        }
    return out
