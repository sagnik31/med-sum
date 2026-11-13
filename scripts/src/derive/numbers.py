"""
Numbers, reference ranges and computed flags, parsed from printed text.

A number is parsed only when the text is a number and nothing else:
"2,45,000" is 245000, but "< 0.5", "2-4", "Nil" and "1,5" have no
number. Every printed range is kept; a flag is computed only when exactly
one range clearly applies, and the printed flag is never overwritten.
See docs/adr/0003-printed-and-derived-values.md.
"""

import re
from dataclasses import dataclass

from derive.units import lookup_unit
from record.document import ComputedFlag, ReferenceRange

# Digit grouping is either Indian (1,50,000) or international (150,000);
# a validly grouped number means the same in both, so commas just go.
_INDIAN = r"\d{1,2}(?:,\d{2})*,\d{3}"
_INTERNATIONAL = r"\d{1,3}(?:,\d{3})+"
_NUMBER = re.compile(rf"(?:{_INDIAN}|{_INTERNATIONAL}|\d+)(?:\.\d+)?")


def parse_number(text: str | None) -> float | None:
    """The number a text is, or None when it is anything else."""
    if text is None:
        return None
    text = text.strip()
    if not _NUMBER.fullmatch(text):
        return None
    return float(text.replace(",", ""))


# -------------------------------------------------------------------
# Reference ranges
# -------------------------------------------------------------------
_NUM = r"\d[\d,]*(?:\.\d+)?"
_BETWEEN = re.compile(rf"(?P<low>{_NUM})\s*(?:-|–|—|to)\s*(?P<high>{_NUM})", re.I)
_BOUND = re.compile(
    rf"(?P<op><=|>=|≤|≥|<|>|up\s*to|less\s+than|more\s+than|greater\s+than)"
    rf"\s*(?P<num>{_NUM})",
    re.I,
)
_LABEL = re.compile(r"(?P<label>[^\d:<>=≤≥]*[A-Za-z][^\d:<>=≤≥]*?)\s*:\s*(?P<body>.+)")
# Greedy, so the range ends at its last number: "1.5 - 4.5 lakh/cumm".
_UNIT_SUFFIX = re.compile(r"(?P<range>.*\d)\s+(?P<unit>[^\d\s].*)")

_UPPER = {"<": False, "≤": True, "<=": True, "upto": True, "lessthan": False}
_LOWER = {">": False, "≥": True, ">=": True, "morethan": False, "greaterthan": False}


@dataclass(frozen=True)
class ParsedRange:
    """A printed range, parsed. `parsed` is False when no bounds could be read."""

    text: str
    low: float | None = None
    high: float | None = None
    low_inclusive: bool = True
    high_inclusive: bool = True
    label: str | None = None
    unit: str | None = None  # a unit printed in the range itself
    parsed: bool = False

    def reference(self) -> ReferenceRange:
        return ReferenceRange(
            text=self.text, low=self.low, high=self.high, label=self.label
        )


def parse_range(text: str) -> ParsedRange:
    """
    "12.0 - 15.0", "< 150", ">= 6.5", "Up to 40", each optionally labelled
    ("Prediabetes: 5.7 - 6.4") and followed by a unit ("1.5 - 4.5 lakh/cumm").
    Anything else ("Nil", "Absent", "See note") keeps its text only.
    """
    body = text.strip()
    label = None
    if m := _LABEL.fullmatch(body):
        label, body = m["label"].strip(), m["body"].strip()

    unit = None
    parsed = _bounds(body)
    if parsed is None and (m := _UNIT_SUFFIX.fullmatch(body)):
        parsed = _bounds(m["range"])
        unit = m["unit"].strip()
    if parsed is None:
        return ParsedRange(text=text)
    return ParsedRange(text=text, label=label, unit=unit, parsed=True, **parsed)


def _bounds(body: str) -> dict | None:
    if m := _BETWEEN.fullmatch(body):
        low, high = parse_number(m["low"]), parse_number(m["high"])
        if low is None or high is None:
            return None
        return {"low": low, "high": high}
    if m := _BOUND.fullmatch(body):
        num = parse_number(m["num"])
        if num is None:
            return None
        op = re.sub(r"\s+", "", m["op"]).lower()
        if op in _UPPER:
            return {"high": num, "high_inclusive": _UPPER[op]}
        return {"low": num, "low_inclusive": _LOWER[op]}
    return None


def has_digits(text: str) -> bool:
    return any(c.isdigit() for c in text)


# -------------------------------------------------------------------
# Computed flag
# -------------------------------------------------------------------
def compute_flag(
    value: float | None, ranges: list[ParsedRange], value_unit: str | None
) -> ComputedFlag | None:
    """
    Low, normal or high, only when the value is a number and exactly one
    range is printed, with bounds, in the value's unit. Several ranges
    (HbA1c bands, sex-specific ranges) give no flag: choosing one would be
    a guess.
    """
    if value is None or len(ranges) != 1:
        return None
    r = ranges[0]
    if not r.parsed or (r.low is None and r.high is None):
        return None
    if r.unit is not None and not same_unit(r.unit, value_unit):
        return None
    if r.low is not None and (
        value < r.low or (value == r.low and not r.low_inclusive)
    ):
        return ComputedFlag.LOW
    if r.high is not None and (
        value > r.high or (value == r.high and not r.high_inclusive)
    ):
        return ComputedFlag.HIGH
    return ComputedFlag.NORMAL


def same_unit(a: str | None, b: str | None) -> bool:
    """Two printed units are the same unit (by the unit table, else by text)."""
    if a is None or b is None:
        return a is b
    ua, ub = lookup_unit(a), lookup_unit(b)
    if ua is not None and ub is not None:
        return ua.symbol == ub.symbol
    return a.strip() == b.strip()
