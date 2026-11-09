"""
Score one predicted document record against its answer key.

How items are paired (a model's ids and line numbers never match the key's):
- Within each group (observations, medications, ...), a predicted item is
  paired with a key item by its identity text (test name, medicine name,
  remark text, ...), compared case-insensitively after NFKC and spacing,
  with a similarity of at least IDENTITY_MATCH (90%). So a one-letter
  misspelling still pairs and costs that one field; a name further off
  is a missed item plus an invented one.
- Ties (two rows with the same name) are broken by how alike the cited
  quotes are, then by page. Each item pairs at most once.

How fields are scored, on paired items (one field = one printed value):
- Values are verbatim, so they compare exactly, like the validators:
  case-sensitive, ignoring only Unicode forms (NFKC) and spacing.
- key value and equal prediction: true positive; key value, prediction
  null: false negative; key null, prediction set: false positive; both
  set but different: one of each. Both null counts for nothing.
- List fields (reference ranges, clinicians) compare as multisets.
- An unpaired key item counts every field it has as a false negative (a
  miss); an unpaired predicted item counts every field as a false
  positive and is listed as hallucinated.

Derived values (numbers, flags, schedules, dates) are code's work on the
printed values; they are scored the same way but kept apart, so a wrong
reading and a derivation bug can be told apart.

Citations are judged by quote, not line number: same page, and the quotes
(ignoring case, spacing and table pipes) at least CITATION_MATCH alike.

Unsafe errors are what a clinician could act on wrongly, counted apart
for the model choice (choose.py): an invented lab result, medicine or
allergy (UNSAFE_ITEMS), or a wrong or invented value in a clinical field
of a paired item (UNSAFE_FIELDS). A missing value is not unsafe here;
recall covers it. The rate is unsafe errors per clinical item in the key.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Callable, Iterable

from record.common import Citation, PrintedText
from record.document import DocumentRecord
from record.printed import RemarkScope

IDENTITY_MATCH = 0.9
CITATION_MATCH = 0.9

UNSAFE_ITEMS = ("observations", "medications", "allergies")
UNSAFE_FIELDS = {
    "observations": ("value_text", "unit", "flag_printed"),
    "medications": ("name", "strength", "dose", "frequency"),
    "allergies": ("substance",),
}
INVENTED = "(invented item)"  # FieldError.field of an unsafe invented item

GROUPS = ("observations", "medications", "conditions", "remarks", "vitals", "allergies")
HEADER_GROUPS = ("patient", "document")


# -------------------------------------------------------------------
# Results
# -------------------------------------------------------------------
@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn)

    @property
    def precision(self) -> float | None:
        return _div(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return _div(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float | None:
        return _div(2 * self.tp, 2 * self.tp + self.fp + self.fn)

    def as_dict(self) -> dict:
        return {**asdict(self), "precision": self.precision, "recall": self.recall,
                "f1": self.f1}  # fmt: skip


@dataclass
class Ratio:
    correct: int = 0
    total: int = 0

    def __add__(self, other: "Ratio") -> "Ratio":
        return Ratio(self.correct + other.correct, self.total + other.total)

    def add(self, ok: bool) -> None:
        self.total += 1
        self.correct += int(ok)

    @property
    def value(self) -> float | None:
        return _div(self.correct, self.total)

    def as_dict(self) -> dict:
        return {**asdict(self), "value": self.value}


@dataclass(frozen=True)
class FieldError:
    group: str
    key_id: str | None  # None for an invented item
    pred_id: str | None  # None for a missed item
    field: str
    expected: Any
    got: Any


@dataclass(frozen=True)
class ItemRef:
    """A missed key item or a hallucinated predicted item."""

    group: str
    id: str
    label: str


@dataclass
class DocumentScore:
    document_id: str
    type_expected: str
    type_got: str | None  # None when there was no record (the run failed)
    fields: dict[str, Counts] = field(default_factory=dict)
    derived: dict[str, Counts] = field(default_factory=dict)
    items: dict[str, Counts] = field(default_factory=dict)
    citations: Ratio = field(default_factory=Ratio)
    remark_attribution: Ratio = field(default_factory=Ratio)
    frequency: Ratio = field(default_factory=Ratio)
    missed: list[ItemRef] = field(default_factory=list)
    hallucinated: list[ItemRef] = field(default_factory=list)
    errors: list[FieldError] = field(default_factory=list)
    unsafe: list[FieldError] = field(default_factory=list)
    clinical_items: int = 0  # key items in UNSAFE_ITEMS groups

    @property
    def unsafe_rate(self) -> float | None:
        return _div(len(self.unsafe), self.clinical_items)

    @property
    def type_correct(self) -> bool:
        return self.type_got == self.type_expected

    @property
    def all_fields(self) -> Counts:
        return _total(self.fields.values())

    @property
    def all_derived(self) -> Counts:
        return _total(self.derived.values())

    @property
    def medicine_recall(self) -> float | None:
        return self.items["medications"].recall

    def as_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "type_expected": self.type_expected,
            "type_got": self.type_got,
            "type_correct": self.type_correct,
            "all_fields": self.all_fields.as_dict(),
            "all_derived": self.all_derived.as_dict(),
            "fields": {g: c.as_dict() for g, c in self.fields.items()},
            "derived": {g: c.as_dict() for g, c in self.derived.items()},
            "items": {g: c.as_dict() for g, c in self.items.items()},
            "citations": self.citations.as_dict(),
            "remark_attribution": self.remark_attribution.as_dict(),
            "frequency": self.frequency.as_dict(),
            "missed": [asdict(m) for m in self.missed],
            "hallucinated": [asdict(h) for h in self.hallucinated],
            "errors": [_jsonable(asdict(e)) for e in self.errors],
            "unsafe": [_jsonable(asdict(e)) for e in self.unsafe],
            "clinical_items": self.clinical_items,
            "unsafe_rate": self.unsafe_rate,
        }


@dataclass
class Summary:
    """Scores added up over documents (micro-averaged)."""

    documents: int = 0
    types: Ratio = field(default_factory=Ratio)
    fields: dict[str, Counts] = field(default_factory=dict)
    derived: dict[str, Counts] = field(default_factory=dict)
    items: dict[str, Counts] = field(default_factory=dict)
    citations: Ratio = field(default_factory=Ratio)
    remark_attribution: Ratio = field(default_factory=Ratio)
    frequency: Ratio = field(default_factory=Ratio)
    hallucinated: int = 0
    missed: int = 0
    unsafe: int = 0
    clinical_items: int = 0

    @property
    def unsafe_rate(self) -> float | None:
        return _div(self.unsafe, self.clinical_items)

    @property
    def all_fields(self) -> Counts:
        return _total(self.fields.values())

    @property
    def all_derived(self) -> Counts:
        return _total(self.derived.values())

    @property
    def medicine_recall(self) -> float | None:
        return self.items.get("medications", Counts()).recall

    def as_dict(self) -> dict:
        return {
            "documents": self.documents,
            "types": self.types.as_dict(),
            "all_fields": self.all_fields.as_dict(),
            "all_derived": self.all_derived.as_dict(),
            "fields": {g: c.as_dict() for g, c in self.fields.items()},
            "derived": {g: c.as_dict() for g, c in self.derived.items()},
            "items": {g: c.as_dict() for g, c in self.items.items()},
            "medicine_recall": self.medicine_recall,
            "citations": self.citations.as_dict(),
            "remark_attribution": self.remark_attribution.as_dict(),
            "frequency": self.frequency.as_dict(),
            "hallucinated": self.hallucinated,
            "missed": self.missed,
            "unsafe": self.unsafe,
            "clinical_items": self.clinical_items,
            "unsafe_rate": self.unsafe_rate,
        }


def summarise(scores: Iterable[DocumentScore]) -> Summary:
    s = Summary()
    for d in scores:
        s.documents += 1
        s.types.add(d.type_correct)
        for mine, theirs in ((s.fields, d.fields), (s.derived, d.derived),
                             (s.items, d.items)):  # fmt: skip
            for group, counts in theirs.items():
                mine[group] = mine.get(group, Counts()) + counts
        s.citations += d.citations
        s.remark_attribution += d.remark_attribution
        s.frequency += d.frequency
        s.hallucinated += len(d.hallucinated)
        s.missed += len(d.missed)
        s.unsafe += len(d.unsafe)
        s.clinical_items += d.clinical_items
    return s


# -------------------------------------------------------------------
# Text
# -------------------------------------------------------------------
def _norm(text: str) -> str:
    """How values compare: NFKC, spacing collapsed, case kept."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _loose(text: str) -> str:
    """How identities compare: also case-insensitive."""
    return _norm(text).casefold()


def _quote_key(text: str) -> str:
    """How quotes compare: no case, no spacing, no table pipes."""
    return re.sub(r"[\s|]+", "", unicodedata.normalize("NFKC", text)).casefold()


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def citation_matches(pred: Citation, key: Citation) -> bool:
    return pred.page == key.page and (
        similarity(_quote_key(pred.quote), _quote_key(key.quote)) >= CITATION_MATCH
    )


# -------------------------------------------------------------------
# What is scored, per group
# -------------------------------------------------------------------
@dataclass(frozen=True)
class GroupSpec:
    identity: Callable[[Any], str]  # printed -> the text items pair on
    fields: tuple[str, ...]  # printed fields that are scored
    derived_skip: tuple[str, ...] = ()  # derived fields scored elsewhere


SPECS = {
    "observations": GroupSpec(
        lambda p: p.test_name,
        (
            "panel",
            "test_name",
            "value_text",
            "unit",
            "reference_ranges",
            "flag_printed",
        ),
    ),
    "medications": GroupSpec(
        lambda p: p.name,
        (
            "form",
            "name",
            "strength",
            "dose",
            "frequency",
            "route",
            "duration",
            "instructions",
        ),
    ),
    "conditions": GroupSpec(lambda p: p.text, ("text", "kind")),
    "remarks": GroupSpec(
        lambda p: p.text,
        ("text", "kind", "scope", "test_name", "panel"),
        derived_skip=("observation_id",),  # ids differ; see remark attribution
    ),
    "vitals": GroupSpec(lambda p: p.name, ("name", "value_text", "unit")),
    "allergies": GroupSpec(lambda p: p.text, ("text", "substance", "reaction")),
}

PATIENT_FIELDS = ("name", "age", "sex", "date_of_birth", "patient_id")
DOCUMENT_FIELDS = ("facility", "report_date", "collection_date", "prescription_date")


def _value(v: Any) -> Any:
    """A field's value as compared: text normalised, enums by value, lists kept."""
    if v is None:
        return None
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, PrintedText):
        return _norm(v.text)
    if isinstance(v, str):
        return _norm(v)
    if isinstance(v, list):
        return [_value(x) for x in v] or None
    return v


def _compare(expected: Any, got: Any, counts: Counts) -> bool:
    """Add one field to the counts; True when it was right (or both empty)."""
    if isinstance(expected, list) or isinstance(got, list):
        e, g = Counter(expected or []), Counter(got or [])
        counts.tp += sum((e & g).values())
        counts.fp += sum((g - e).values())
        counts.fn += sum((e - g).values())
        return e == g
    if expected is None and got is None:
        return True
    if expected == got:
        counts.tp += 1
        return True
    if expected is not None:
        counts.fn += 1
    if got is not None:
        counts.fp += 1
    return False


# -------------------------------------------------------------------
# Pairing
# -------------------------------------------------------------------
def pair_items(pred: list, key: list, spec: GroupSpec) -> list[tuple[int, int]]:
    """(pred index, key index) pairs, best first; each index used at most once."""
    candidates = []
    for i, p in enumerate(pred):
        for j, k in enumerate(key):
            ident = similarity(_loose(spec.identity(p.printed)),
                               _loose(spec.identity(k.printed)))  # fmt: skip
            if ident < IDENTITY_MATCH:
                continue
            pc, kc = p.printed.citation, k.printed.citation
            quote = similarity(_quote_key(pc.quote), _quote_key(kc.quote))
            candidates.append((-ident, -quote, pc.page != kc.page, j, i))
    used_p, used_k, pairs = set(), set(), []
    for *_, j, i in sorted(candidates):
        if i not in used_p and j not in used_k:
            used_p.add(i)
            used_k.add(j)
            pairs.append((i, j))
    return pairs


# -------------------------------------------------------------------
# Scoring
# -------------------------------------------------------------------
def score_document(pred: DocumentRecord | None, key: DocumentRecord) -> DocumentScore:
    """
    Score `pred` (the pipeline's record, or None when the run produced
    none) against `key` (the answer key of the same document).
    """
    score = DocumentScore(
        document_id=key.document.document_id,
        type_expected=key.document.type.value,
        type_got=pred.document.type.value if pred is not None else None,
    )
    _score_header(pred, key, score)
    observation_map: dict[str, str] = {}  # pred observation id -> key id
    pairs_by_group = {}
    for group in GROUPS:
        pred_items = getattr(pred, group) if pred is not None else []
        key_items = getattr(key, group)
        pairs = pair_items(pred_items, key_items, SPECS[group])
        pairs_by_group[group] = [(pred_items[i], key_items[j]) for i, j in pairs]
        if group == "observations":
            observation_map = {p.id: k.id for p, k in pairs_by_group[group]}
        _score_group(group, pred_items, key_items, pairs, score)
    for p, k in pairs_by_group["remarks"]:
        score.remark_attribution.add(_attributed(p, k, observation_map))
    for p, k in pairs_by_group["medications"]:
        if k.printed.frequency is not None:
            score.frequency.add(
                _value(p.printed.frequency) == _value(k.printed.frequency)
                and p.derived.schedule == k.derived.schedule
            )
    return score


def _score_group(group, pred_items, key_items, pairs, score: DocumentScore) -> None:
    spec = SPECS[group]
    fields, derived = Counts(), Counts()
    paired_p = {i for i, _ in pairs}
    paired_k = {j for _, j in pairs}
    score.items[group] = Counts(
        tp=len(pairs), fp=len(pred_items) - len(pairs), fn=len(key_items) - len(pairs)
    )
    unsafe_fields = UNSAFE_FIELDS.get(group, ())
    if group in UNSAFE_ITEMS:
        score.clinical_items += len(key_items)
    for i, j in pairs:
        p, k = pred_items[i], key_items[j]
        for name in spec.fields:
            e, g = _value(getattr(k.printed, name)), _value(getattr(p.printed, name))
            if not _compare(e, g, fields):
                error = FieldError(group, k.id, p.id, name, e, g)
                score.errors.append(error)
                if name in unsafe_fields and g is not None:
                    score.unsafe.append(error)
        _score_derived(group, p.derived if hasattr(p, "derived") else None,
                       k.derived if hasattr(k, "derived") else None, spec,
                       derived, score, k.id, p.id)  # fmt: skip
        score.citations.add(citation_matches(p.printed.citation, k.printed.citation))
    for j, k in enumerate(key_items):
        if j not in paired_k:
            score.missed.append(ItemRef(group, k.id, spec.identity(k.printed)))
            for name in spec.fields:
                _compare(_value(getattr(k.printed, name)), None, fields)
            _score_derived(group, None, getattr(k, "derived", None), spec, derived)
    for i, p in enumerate(pred_items):
        if i not in paired_p:
            label = spec.identity(p.printed)
            score.hallucinated.append(ItemRef(group, p.id, label))
            if group in UNSAFE_ITEMS:
                score.unsafe.append(
                    FieldError(group, None, p.id, INVENTED, None, label)
                )
            for name in spec.fields:
                _compare(None, _value(getattr(p.printed, name)), fields)
            _score_derived(group, getattr(p, "derived", None), None, spec, derived)
    score.fields[group] = fields
    score.derived[group] = derived


def _score_derived(group, pred, key, spec, counts, score=None, key_id=None,
                   pred_id=None) -> None:  # fmt: skip
    e = key.model_dump(mode="json") if key is not None else {}
    g = pred.model_dump(mode="json") if pred is not None else {}
    for name in sorted(set(e) | set(g)):
        if name in spec.derived_skip:
            continue
        ev, gv = _derived_value(e.get(name)), _derived_value(g.get(name))
        # A list of parsed ranges is one value: right or wrong as a whole.
        if not _compare(_freeze(ev), _freeze(gv), counts) and score is not None:
            score.errors.append(FieldError(group, key_id, pred_id, f"derived.{name}",
                                           ev, gv))  # fmt: skip


def _derived_value(v: Any) -> Any:
    """An empty list or a False flag counts as 'nothing derived' (0.0 does not)."""
    if v is False or v == []:
        return None
    return v


def _freeze(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return repr(v)
    return v


def _score_header(pred: DocumentRecord | None, key: DocumentRecord, score) -> None:
    pp = pred.patient.printed if pred is not None and pred.patient else None
    kp = key.patient.printed if key.patient else None
    patient = Counts()
    for name in PATIENT_FIELDS:
        _header_field("patient", name, getattr(pp, name, None),
                      getattr(kp, name, None), patient, score)  # fmt: skip
    score.fields["patient"] = patient

    pd = pred.document.printed if pred is not None else None
    kd = key.document.printed
    document = Counts()
    for name in DOCUMENT_FIELDS:
        _header_field("document", name, getattr(pd, name, None), getattr(kd, name),
                      document, score)  # fmt: skip
    pred_clin = pd.clinicians if pd is not None else []
    e, g = _value(kd.clinicians), _value(pred_clin)
    if not _compare(e, g, document):
        score.errors.append(FieldError("document", "document", "document",
                                       "clinicians", e, g))  # fmt: skip
    by_text = {_norm(c.text): c.citation for c in pred_clin}
    for c in kd.clinicians:
        if (pc := by_text.get(_norm(c.text))) is not None:
            score.citations.add(citation_matches(pc, c.citation))
    score.fields["document"] = document

    dates = Counts()
    _score_derived("document", pred.document.derived if pred is not None else None,
                   key.document.derived, GroupSpec(lambda p: "", ()), dates, score,
                   "document", "document")  # fmt: skip
    score.derived["document"] = dates


def _header_field(group, name, pred: PrintedText | None, key: PrintedText | None,
                  counts: Counts, score: DocumentScore) -> None:  # fmt: skip
    e, g = _value(key), _value(pred)
    if not _compare(e, g, counts):
        score.errors.append(FieldError(group, group, group, name, e, g))
    if pred is not None and key is not None:
        score.citations.add(citation_matches(pred.citation, key.citation))


def _attributed(pred, key, observation_map: dict[str, str]) -> bool:
    """Whether a paired remark is attached where the key attaches it."""
    p, k = pred.printed, key.printed
    if p.scope is not k.scope:
        return False
    if k.scope is RemarkScope.DOCUMENT:
        return True
    if k.scope is RemarkScope.PANEL:
        return _value(p.panel) == _value(k.panel)
    if key.derived.observation_id is not None:
        return observation_map.get(pred.derived.observation_id) == (
            key.derived.observation_id
        )
    return _value(p.test_name) == _value(k.test_name) and (
        _value(p.panel) == _value(k.panel)
    )


# -------------------------------------------------------------------
def _div(a: int, b: int) -> float | None:
    return a / b if b else None


def _total(counts: Iterable[Counts]) -> Counts:
    total = Counts()
    for c in counts:
        total += c
    return total


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
