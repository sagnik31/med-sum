"""
Document type: the model's label, checked against keywords. See docs/adr/0002-two-model-steps.md.

The keyword check counts which signals of a lab report and of a
prescription appear in the transcription. It names a type only when that
type has at least MIN_SIGNALS signals and at least twice as many as the
other; otherwise it has no opinion.

How the two combine:
- the model's label is used when the keywords agree or have no opinion;
- the keywords' type is used when the model gave no usable label, or said
  `other` while the keywords clearly name a type (small models say other
  when unsure; the validators still check the parse that follows);
- when the model and the keywords name the two different parsed types, the
  document is `other`, so it goes to a person instead of being parsed with
  the wrong schema.
"""

import re
from dataclasses import dataclass

from record.printed import DocumentType

MIN_SIGNALS = 2

_LAB = {
    "reference range": r"\b(biological\s+)?ref(erence|\.)?\s*(range|interval|value)s?\b",
    "normal range": r"\bnormal\s+(range|value)s?\b",
    "result column": r"\|\s*results?\s*\|",
    "unit column": r"\|\s*units?\s*\|",
    "test column": r"\|\s*(test|investigation|parameter)s?(\s+name)?\s*\|",
    "sample": r"\b(sample|specimen)\b",
    "collected": r"\bcollect(ed|ion)\b",
    "laboratory": r"\b(laborator(y|ies)|diagnostics|patholog(y|ist))\b",
}
_PRESCRIPTION = {
    "rx": r"(\bR[xX]\b|℞)",
    "dosage form": r"\b(tab|cap|syp|inj|caps|tabs)\b\.?",
    "dose pattern": r"\b[0-9½]+\s*-\s*[0-9½]+\s*-\s*[0-9½]+\b",
    "frequency": r"\b(OD|BD|BID|TDS|TID|QID|HS|SOS|PRN|stat)\b",
    "food timing": r"\b(before|after|with)\s+(food|meals?|breakfast|lunch|dinner)\b",
    "medicine column": r"\|\s*(medicine|drug)s?(\s+name)?\s*\|",
}
# Matched case-sensitively: "od", "hs" and "stat" are ordinary words too.
_CASE_SENSITIVE = {"rx", "frequency"}


def _compile(table: dict[str, str]) -> dict[str, re.Pattern]:
    return {
        name: re.compile(p, 0 if name in _CASE_SENSITIVE else re.IGNORECASE)
        for name, p in table.items()
    }


LAB_SIGNALS = _compile(_LAB)
PRESCRIPTION_SIGNALS = _compile(_PRESCRIPTION)


@dataclass(frozen=True)
class KeywordCheck:
    lab_signals: tuple[str, ...]
    prescription_signals: tuple[str, ...]

    @property
    def type(self) -> DocumentType | None:
        lab, rx = len(self.lab_signals), len(self.prescription_signals)
        if lab >= MIN_SIGNALS and lab >= 2 * rx:
            return DocumentType.LAB_REPORT
        if rx >= MIN_SIGNALS and rx >= 2 * lab:
            return DocumentType.PRESCRIPTION
        return None


def keyword_check(text: str) -> KeywordCheck:
    def found(signals):
        return tuple(name for name, p in signals.items() if p.search(text))

    return KeywordCheck(found(LAB_SIGNALS), found(PRESCRIPTION_SIGNALS))


@dataclass(frozen=True)
class Decision:
    type: DocumentType
    rule: str  # which part of the rule above decided it, for the step's output


def decide(model: DocumentType | None, keywords: DocumentType | None) -> Decision:
    """Combine the model's label (None if unusable) with the keyword check."""
    if model is None:
        if keywords is None:
            return Decision(DocumentType.OTHER, "no model label, no keyword type")
        return Decision(keywords, "no model label; keyword type")
    if keywords is None or keywords is model:
        return Decision(model, "model label")
    if model is DocumentType.OTHER:
        return Decision(keywords, "model said other; keyword type")
    return Decision(DocumentType.OTHER, "model and keywords disagree")


def disagree(model: DocumentType | None, keywords: DocumentType | None) -> bool:
    """
    Whether the model's label and the keyword check name different types
    (signal LC-15): the wrong parse schema may be in use. No usable label,
    or keywords with no opinion, is not a disagreement.
    """
    return model is not None and keywords is not None and model is not keywords
