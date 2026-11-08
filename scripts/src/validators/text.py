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


def contains(haystack: str, needle: str) -> bool:
    """
    `needle` is in `haystack` as a whole token: "10" is not found in
    "10.4" or "110", "L" not in "g/dL". Edges that aren't letters or
    digits (a unit's "/", a "<") have no boundary to respect.
    """
    h, n = normalise(haystack), normalise(needle)
    if not n:
        return False
    start = 0
    while (i := h.find(n, start)) != -1:
        if _left_ok(h, i, n) and _right_ok(h, i + len(n), n):
            return True
        start = i + 1
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
