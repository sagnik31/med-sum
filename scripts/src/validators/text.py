"""
Text comparison for the validators. Values are verbatim, so comparison is
case-sensitive; only Unicode forms (NFKC: ligatures, superscripts, the
micro sign) and spacing are ignored.
"""

import re
import unicodedata

from record.common import Citation
from record.document import DocumentRecord


def normalise(text: str) -> str:
    """NFKC, whitespace collapsed to single spaces."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def squash(text: str) -> str:
    """NFKC with all whitespace removed: for PDF text layers, which split cells."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def gaps(text: str) -> frozenset[int]:
    """
    The positions in squash(text) that whitespace was printed before: where
    two numbers that squashing joined ("5.7" and "4.0 - 6.0") really end.
    """
    found, at = set(), 0
    for word in unicodedata.normalize("NFKC", text).split():
        found.add(at)
        at += len(word)
    return frozenset(found)


def _inside_number(
    layer: str, i: int, j: int, needle: str, breaks: frozenset[int]
) -> bool:
    """
    Whether layer[i:j] is a number cut out of a longer one: "4.2" inside
    "14.2" or "4.25". A break between them, where the layer had whitespace,
    makes them two numbers.
    """

    def digit(n: int) -> bool:
        return 0 <= n < len(layer) and layer[n].isdigit()

    if needle[0].isdigit() and i not in breaks and i > 0:
        if digit(i - 1):
            return True
        if layer[i - 1] in ".," and i - 1 not in breaks and digit(i - 2):
            return True
    if needle[-1].isdigit() and j not in breaks:
        if digit(j):
            return True
        if j < len(layer) and layer[j] in ".," and j + 1 not in breaks and digit(j + 1):
            return True
    return False


def contains(haystack: str, needle: str) -> bool:
    """
    `needle` is in `haystack` as a whole token: "10" is not found in
    "10.4" or "110", "L" not in "g/dL". Edges that aren't letters or
    digits (a unit's "/") have no boundary to respect. Nor is it found
    where the printed text qualifies it (see `qualified`): "0.5" is not in
    "< 0.5", "Reactive" not in "Non-Reactive".
    """
    h, n = normalise(haystack), normalise(needle)
    if not n:
        return False
    start = 0
    while (i := h.find(n, start)) != -1:
        if (
            _left_ok(h, i, n)
            and _right_ok(h, i + len(n), n)
            and not qualified(h[:i], n)
        ):
            return True
        start = i + 1
    return False


def squashed_in(
    haystack: str,
    needle: str,
    *,
    layer: str | None = None,
    at: int = 0,
    breaks: frozenset[int] | None = None,
) -> bool:
    """
    `needle` is in `haystack`, both already squashed (no spacing), at a
    place the printed text doesn't qualify it: the text layer's form of
    `contains`. With `breaks` (the layer's `gaps`), a number is not found
    inside a longer one; `haystack` is then the part of `layer` that starts
    at `at`.
    """
    if not needle:
        return False
    whole = haystack if layer is None else layer
    start = 0
    while (i := haystack.find(needle, start)) != -1:
        cut = breaks is not None and _inside_number(
            whole, at + i, at + i + len(needle), needle, breaks
        )
        if not cut and not qualified(haystack[:i], needle, squashed=True):
            return True
        start = i + 1
    return False


# What turns a printed value into another one when it comes just before
# it: a comparator ("< 0.5" is not 0.5), a sign stuck to a number ("-5"
# is not 5), and a negating word ("Non-Reactive" is not "Reactive", "Not
# Detected" is not "Detected"). A value found only after one of these was
# not read in full: it is an error for the parse to repair, never a match.
# See docs/adr/0004-deterministic-checks-decide-routing.md.
_COMPARATORS = "<>≤≥≦≧"
# One at the end of a text, whole: "<=" and ">=" too (and "</=").
_COMPARATOR_AT_END = re.compile(rf"[{_COMPARATORS}]\s*/?\s*=?$|=\s*[<>]$")
_SIGNS = "-+−"
_NEGATIONS = ("non", "not", "no")


def qualified(before: str, needle: str, *, squashed: bool = False) -> bool:
    """
    Whether the text just before a match of `needle` qualifies it, so the
    match is not the value as printed. `before` is all the text before the
    match. A needle that starts with the qualifier itself ("< 0.5") is
    matched with it, so nothing before it is checked. In `squashed` text
    a negating word has no space to start at ("HBsAgNonReactive"), so it
    counts wherever it ends; at worst a value is flagged for a clinician.
    """
    if not needle or needle[0] in _COMPARATORS or needle[0] in _SIGNS:
        return False
    rest = before.rstrip()
    if _COMPARATOR_AT_END.search(rest):
        return True
    if needle[0].isdigit():
        # A sign right before the number, not a dash between two numbers
        # ("1-6" is a range, so its "6" is still found).
        return (
            bool(before)
            and before[-1] in _SIGNS
            and (len(before) == 1 or not before[-2].isalnum())
        )
    if needle[0].isalpha():
        word = rest.rstrip("-").rstrip()
        for neg in _NEGATIONS:
            if word.casefold().endswith(neg) and (
                squashed or len(word) == len(neg) or not word[-len(neg) - 1].isalpha()
            ):
                return True
    return False


def _left_ok(h: str, i: int, n: str) -> bool:
    if i == 0 or not n[0].isalnum():
        return True
    c = h[i - 1]
    if c.isalnum():
        return False
    # "5" inside "0.5" or "2,45,000".
    return not (c in ".," and n[0].isdigit() and i >= 2 and h[i - 2].isdigit())


def _right_ok(h: str, j: int, n: str) -> bool:
    if j == len(h) or not n[-1].isalnum():
        return True
    c = h[j]
    if c.isalnum():
        return False
    return not (c in ".," and n[-1].isdigit() and j + 1 < len(h) and h[j + 1].isdigit())


def cited_lines(record: DocumentRecord, citation: Citation) -> list[str] | None:
    """The lines a citation points at, or None when they aren't there."""
    if not 1 <= citation.page <= len(record.pages):
        return None
    page = record.pages[citation.page - 1]
    if citation.last_line > len(page.lines):
        return None
    return page.lines[citation.first_line - 1 : citation.last_line]


def cited_numbers(record: DocumentRecord, citation: Citation) -> range:
    """
    The line numbers a citation points at that the page has. A citation may
    claim any last line, so nothing counts up to it unchecked.
    """
    if not 1 <= citation.page <= len(record.pages):
        return range(0)
    last = min(citation.last_line, len(record.pages[citation.page - 1].lines))
    return range(citation.first_line, last + 1)


def lines_before(record: DocumentRecord, page: int, line: int) -> list[str]:
    """Every line before (page, line) in reading order, nearest last."""
    out = []
    for p in record.pages[: page - 1]:
        out.extend(p.lines)
    out.extend(record.pages[page - 1].lines[: line - 1])
    return out


def is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def is_blank(line: str) -> bool:
    return not line.strip()
