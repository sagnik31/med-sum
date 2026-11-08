"""
Dose schedules and durations, parsed from a prescription's frequency and
duration as written.

"1-0-1" is one in the morning, none at noon, one at night (twice a day);
BD is twice a day with no times given; SOS is as needed. A weekly dose has
no daily schedule. Durations in days or weeks become days; months don't,
since a month isn't a fixed number of days, and "Continue" has none.
"""

import re
from fractions import Fraction

from record.document import DoseSchedule

_FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4"}
_SLOT = r"(\d+(?:\.\d+)?|\d+/\d+|[½¼¾]|\d+\s*[½¼¾])"
_FIXED = re.compile(rf"{_SLOT}\s*-\s*{_SLOT}\s*-\s*{_SLOT}")
_FOUR = re.compile(rf"{_SLOT}\s*-\s*{_SLOT}\s*-\s*{_SLOT}\s*-\s*{_SLOT}")

TIMES_PER_DAY = {
    "od": 1, "qd": 1, "once daily": 1, "once a day": 1, "daily": 1, "hs": 1,
    "bd": 2, "bid": 2, "twice daily": 2, "twice a day": 2,
    "tds": 3, "tid": 3, "thrice daily": 3, "three times a day": 3,
    "qid": 4, "qds": 4, "four times a day": 4,
}  # fmt: skip
AS_NEEDED = {"sos", "prn", "as needed", "as required", "when required", "if needed"}


def _key(text: str) -> str:
    """Lower case, dots dropped ("B.D." is "bd"), spaces collapsed."""
    return re.sub(r"\s+", " ", text.replace(".", "").casefold()).strip()


def _slot(text: str) -> float:
    for symbol, fraction in _FRACTIONS.items():
        text = text.replace(symbol, f" {fraction}")
    return float(sum(Fraction(part) for part in text.split()))


def parse_frequency(text: str | None) -> DoseSchedule | None:
    """The schedule a frequency states, or None (weekly, STAT, unreadable)."""
    if text is None:
        return None
    text = text.strip()
    if m := _FIXED.fullmatch(text):
        morning, noon, night = (_slot(m[i]) for i in (1, 2, 3))
        return DoseSchedule(
            morning=morning,
            noon=noon,
            night=night,
            times_per_day=float(sum(1 for x in (morning, noon, night) if x)),
        )
    if m := _FOUR.fullmatch(text):
        # Morning, noon, evening, night: no slot for evening, so only a count.
        doses = [_slot(m[i]) for i in (1, 2, 3, 4)]
        return DoseSchedule(times_per_day=float(sum(1 for x in doses if x)))
    key = _key(text)
    if key in TIMES_PER_DAY:
        return DoseSchedule(times_per_day=float(TIMES_PER_DAY[key]))
    if key in AS_NEEDED:
        return DoseSchedule(as_needed=True)
    return None


_DURATION = re.compile(r"(?:x|×|for)?\s*(\d+)\s*(days?|d|weeks?|wks?|w)", re.IGNORECASE)


def parse_duration(text: str | None) -> int | None:
    """Days, from "5 days", "x 14 days", "6 weeks"; None for months or no number."""
    if text is None:
        return None
    m = _DURATION.fullmatch(text.strip().rstrip("."))
    if not m:
        return None
    n = int(m[1])
    return n * 7 if m[2].lower().startswith("w") else n
