"""
Score a transcription (the vision step's numbered lines) against the
dataset's expected transcription.

Markdown furniture is not content: before comparing, each line drops its
heading marks, table pipes become spaces, table separator rows and blank
lines are dropped, and spacing and Unicode forms (NFKC) are normalised.
Case is kept, since values are verbatim.

Reported, per document:
- pages: expected and transcribed counts (a missing page loses all of its
  lines and values; an extra page adds false positives);
- lines: content lines as a multiset per page (precision and recall);
- cer: character error rate, edit distance over expected characters, per
  page then summed;
- values: whether each printed value of the answer key (validators'
  cited_fields) is in the transcription, as a whole token, on the lines
  most like its cited quote (on that page; line numbers are ignored). A
  panel name or a remark's test name, printed above its line, may be
  anywhere on the page. The parse step can only copy what is there;
- layout: of the lines whose content is right, how many keep their shape
  (a heading and its level, a table row and its cell count, or plain
  text). Reported apart from the content scores, since the parse step
  relies on table rows and headings;
- unreadable: lines the model marked `[unreadable]` (signal LC-01).

Value recall also gates the choice of primary model (choose.py).
"""

import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from evals.score import Counts, Ratio
from record.document import DocumentRecord, PageTranscription
from validators.fields import Place, cited_fields
from validators.text import contains

UNREADABLE = "[unreadable]"
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def shaped_lines(lines: Iterable[str]) -> list[tuple[str, str]]:
    """(content, shape) per content line; shape is 'heading N', 'table N cells' or 'text'."""
    out = []
    for line in lines:
        if _SEPARATOR.match(line):
            continue
        raw = unicodedata.normalize("NFKC", line).strip()
        text = re.sub(r"^#+\s*", "", raw).replace("|", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text.startswith("<!--"):
            continue
        if heading := re.match(r"#+", raw):
            shape = f"heading {len(heading.group())}"
        elif raw.startswith("|"):
            shape = f"table {len(raw.strip('|').split('|'))} cells"
        else:
            shape = "text"
        out.append((text, shape))
    return out


def content_lines(lines: Iterable[str]) -> list[str]:
    return [text for text, _ in shaped_lines(lines)]


def layout_differences(
    expected: list[tuple[str, str]], got: list[tuple[str, str]]
) -> tuple[int, list[tuple[str, str, str]]]:
    """
    Lines whose content was transcribed right, and of those, the ones laid
    out differently: (content, expected shape, got shape).
    """
    matcher = SequenceMatcher(
        None, [t for t, _ in expected], [t for t, _ in got], autojunk=False
    )
    compared, differ = 0, []
    for op, i1, i2, j1, _ in matcher.get_opcodes():
        if op != "equal":
            continue
        for k in range(i2 - i1):
            (text, e), (_, g) = expected[i1 + k], got[j1 + k]
            compared += 1
            if e != g:
                differ.append((text, e, g))
    return compared, differ


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insert, delete, substitute all cost 1)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))  # fmt: skip
        previous = current
    return previous[-1]


def page_edits(expected: list[str], got: list[str]) -> int:
    """
    Character edits between two pages of content lines. Lines are aligned
    first, so only changed blocks pay for a character-level distance;
    lines (and the newline between them) count as characters.
    """
    edits = 0
    matcher = SequenceMatcher(None, expected, got, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        a, b = "\n".join(expected[i1:i2]), "\n".join(got[j1:j2])
        edits += edit_distance(a, b) if a and b else len(a) + len(b)
    return edits


def best_window(lines: list[str], quote: str, size: int) -> str:
    """
    The `size` consecutive transcribed lines most like the cited quote,
    joined: where the transcription has that row, wherever it numbered it.
    """
    target = _squash(quote)
    best, best_ratio = "", -1.0
    for i in range(max(len(lines) - size + 1, 0)):
        window = "\n".join(lines[i : i + size])
        ratio = SequenceMatcher(None, _squash(window), target, autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = window, ratio
    return best


def _squash(text: str) -> str:
    return re.sub(r"[\s|]+", "", unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class MissingValue:
    item_id: str
    field: str
    text: str
    page: int


@dataclass(frozen=True)
class LayoutDifference:
    page: int
    text: str
    expected: str
    got: str


@dataclass
class TranscriptionScore:
    document_id: str
    pages_expected: int
    pages_got: int
    lines: Counts = field(default_factory=Counts)
    char_edits: int = 0
    chars_expected: int = 0
    values: Ratio = field(default_factory=Ratio)
    layout: Ratio = field(default_factory=Ratio)
    unreadable: int = 0
    missing_values: list[MissingValue] = field(default_factory=list)
    layout_differences: list[LayoutDifference] = field(default_factory=list)

    @property
    def cer(self) -> float | None:
        return self.char_edits / self.chars_expected if self.chars_expected else None

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "pages_expected": self.pages_expected,
            "pages_got": self.pages_got,
            "lines": self.lines.as_dict(),
            "char_edits": self.char_edits,
            "chars_expected": self.chars_expected,
            "cer": self.cer,
            "values": self.values.as_dict(),
            "layout": self.layout.as_dict(),
            "unreadable": self.unreadable,
            "missing_values": [asdict(m) for m in self.missing_values],
            "layout_differences": [asdict(d) for d in self.layout_differences],
        }


def score_transcription(
    pred: list[PageTranscription] | None, key: DocumentRecord
) -> TranscriptionScore:
    """Score transcribed pages (None when transcription failed) against the key."""
    pred = pred or []
    got = {p.page: p.lines for p in pred}
    score = TranscriptionScore(
        document_id=key.document.document_id,
        pages_expected=len(key.pages),
        pages_got=len(pred),
    )
    numbers = sorted({p.page for p in key.pages} | set(got))
    expected = {p.page: p.lines for p in key.pages}
    for n in numbers:
        es, gs = shaped_lines(expected.get(n, [])), shaped_lines(got.get(n, []))
        e, g = [t for t, _ in es], [t for t, _ in gs]
        compared, differ = layout_differences(es, gs)
        score.layout += Ratio(correct=compared - len(differ), total=compared)
        score.layout_differences += [LayoutDifference(n, *d) for d in differ]
        ec, gc = Counter(e), Counter(g)
        score.lines += Counts(
            tp=sum((ec & gc).values()),
            fp=sum((gc - ec).values()),
            fn=sum((ec - gc).values()),
        )
        score.char_edits += page_edits(e, g)
        score.chars_expected += len("\n".join(e))
        score.unreadable += sum(UNREADABLE in line for line in got.get(n, []))
    page_text = {n: "\n".join(lines) for n, lines in got.items()}
    windows: dict[tuple, str] = {}
    for f in cited_fields(key):
        c = f.citation
        if f.place is Place.ON_OR_ABOVE:
            where = page_text.get(c.page, "")
        else:
            where = windows.get((c.page, c.quote))
            if where is None:
                size = c.last_line - c.first_line + 1
                where = best_window(got.get(c.page, []), c.quote, size)
                windows[(c.page, c.quote)] = where
        found = contains(where, f.text)
        score.values.add(found)
        if not found:
            score.missing_values.append(
                MissingValue(f.item_id, f.field, f.text, f.citation.page)
            )
    return score


@dataclass
class TranscriptionSummary:
    documents: int = 0
    pages_expected: int = 0
    pages_got: int = 0
    lines: Counts = field(default_factory=Counts)
    char_edits: int = 0
    chars_expected: int = 0
    values: Ratio = field(default_factory=Ratio)
    layout: Ratio = field(default_factory=Ratio)
    unreadable: int = 0

    @property
    def cer(self) -> float | None:
        return self.char_edits / self.chars_expected if self.chars_expected else None

    def as_dict(self) -> dict:
        return {**asdict(self), "lines": self.lines.as_dict(),
                "values": self.values.as_dict(), "layout": self.layout.as_dict(),
                "cer": self.cer}  # fmt: skip


def summarise_transcriptions(
    scores: Iterable[TranscriptionScore],
) -> TranscriptionSummary:
    s = TranscriptionSummary()
    for d in scores:
        s.documents += 1
        s.pages_expected += d.pages_expected
        s.pages_got += d.pages_got
        s.lines += d.lines
        s.char_edits += d.char_edits
        s.chars_expected += d.chars_expected
        s.values += d.values
        s.layout += d.layout
        s.unreadable += d.unreadable
    return s
