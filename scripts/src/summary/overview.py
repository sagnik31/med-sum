"""
The summary's overview: a few sentences the model writes from the
computed facts, and the deterministic check that decides whether they
are shown.

The model sees only `facts_text(facts)`, the facts as plain lines. Its
reply is checked word by word:
- every number in it must be a number in the facts;
- every other word must be a word of the facts or one of a fixed list of
  ordinary words (OVERVIEW_WORDS), which holds no test, medicine,
  diagnosis, organ system, severity or judgement.
So a test, medicine or diagnosis that isn't in the facts can't get
through, whatever it is called, and neither can "improving", "worsening",
"abnormal" or a confidence label. A reply that fails is not shown: the
summary says the overview was left out and why (counts only), and the
service logs the counts. The check never edits a reply.
"""

import re
import unicodedata
from dataclasses import dataclass

from summary.facts import Facts, medicine_line

MAX_TOKENS = 400

# Ordinary words an overview may use besides the facts' own. No clinical
# term, judgement or hedge belongs here: adding one is a decision.
OVERVIEW_WORDS = frozenset(
    """
    a an the this that these those it its they them their there here
    and or but nor so yet also both either neither each every all any some
    no not none only just still again
    is are was were be been being has have had do does did
    of in on at to from by for with without within over under between across
    after before during since until through per about into than then
    as such same other another more most less fewer first last latest earlier
    later previous recent next one two three four five six seven eight nine ten
    patient patients document documents report reports record records
    result results value values test tests reading readings
    lab labs laboratory prescription prescriptions prescribed medicine medicines
    stated list lists listed recorded printed noted shown shows show include includes
    included including cover covers covered span spans spanning
    date dated dates undated period time times
    based reported reports latest earliest
    rising falling rose fell trend trends
    information provided available given found
    flag flagged flags range ranges reference outside within above below
    awaiting await awaits review reviewed reviewing clinician clinicians
    allergy allergies
    diagnosis diagnoses impression impressions history
    remark remarks advice comment comments
    vital vitals
    while which who whose where when what how
    january february march april may june july august september october
    november december jan feb mar apr jun jul aug sep sept oct nov dec
    """.split()
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(_norm(text)))


def words(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall(_norm(text).casefold()):
        w = re.sub(r"['’]s$", "", w)
        out.add(w)
    return out


def _known(word: str, vocabulary: set[str]) -> bool:
    if word in vocabulary:
        return True
    # A plural or singular of a known word.
    if word.endswith("s") and word[:-1] in vocabulary:
        return True
    return word + "s" in vocabulary


# -------------------------------------------------------------------
# The facts as text
# -------------------------------------------------------------------
def _when(d) -> str:
    return d.date.isoformat() if d.date is not None else "undated"


def facts_text(facts: Facts) -> str:
    """The computed facts as plain lines: what the model reads, and what its words are checked against."""
    lines = []
    dated = facts.dated
    kinds = {}
    for d in facts.documents:
        kinds[d.type.value.replace("_", " ")] = (
            kinds.get(d.type.value.replace("_", " "), 0) + 1
        )
    kind_text = ", ".join(f"{n} {k}" for k, n in kinds.items())
    span = (
        f", dated {dated[0].date.isoformat()} to {dated[-1].date.isoformat()}"
        if dated
        else ""
    )
    lines.append(f"Documents: {len(facts.documents)} ({kind_text}){span}.")
    if facts.undated:
        lines.append(f"Undated documents: {len(facts.undated)}.")
    if facts.awaiting:
        lines.append(f"Values awaiting review: {facts.awaiting}.")

    for f in facts.attention:
        o = f.item.printed
        flag = []
        if o.flag_printed:
            flag.append(f"printed flag {o.flag_printed}")
        if f.item.derived.flag_computed is not None:
            flag.append(
                f"{f.item.derived.flag_computed.value} against the printed range"
            )
        unit = f" {o.unit}" if o.unit else ""
        lines.append(
            f"Flagged in the latest report: {o.test_name} {o.value_text}{unit}"
            f" ({'; '.join(flag)}), {_when(f.doc)}."
        )
    for s in facts.labs:
        if s.trend is not None:
            dates = ", ".join(_when(v.doc) for v in s.trend.values)
            lines.append(
                f"Trend: {s.name} {s.trend.direction}, based on "
                f"{len(s.trend.values)} reports ({dates})."
            )
    rx = facts.latest_prescription
    if rx is not None:
        meds = "; ".join(" ".join(medicine_line(m.item)) for m in rx.medicines)
        lines.append(f"Medicines on the latest prescription ({_when(rx.doc)}): {meds}.")
    for c in facts.conditions:
        kind = c.item.printed.kind.value.replace("_", " ")
        lines.append(f"Stated {kind}: {c.item.printed.text} ({_when(c.doc)}).")
    for a in facts.allergies:
        lines.append(f"Allergy recorded: {a.item.printed.text} ({_when(a.doc)}).")
    return "\n".join(lines)


# -------------------------------------------------------------------
# The check
# -------------------------------------------------------------------
@dataclass(frozen=True)
class CheckResult:
    unknown_numbers: int
    unknown_words: int

    @property
    def ok(self) -> bool:
        return self.unknown_numbers == 0 and self.unknown_words == 0


def check(overview: str, facts_lines: str) -> CheckResult:
    """Whether every number and word of the overview is in the facts (or is an ordinary word)."""
    vocabulary = words(facts_lines) | OVERVIEW_WORDS
    bad_numbers = numbers(overview) - numbers(facts_lines)
    bad_words = {w for w in words(overview) if not _known(w, vocabulary)}
    return CheckResult(len(bad_numbers), len(bad_words))


# -------------------------------------------------------------------
# The model call
# -------------------------------------------------------------------
@dataclass(frozen=True)
class Overview:
    text: str | None  # shown only when it passed the check
    outcome: str  # "shown", "rejected", "no_model", "empty"
    check: CheckResult | None = None


def write_overview(facts: Facts, system_prompt: str, *, chat=None) -> Overview:
    """The model's overview of the facts, checked. Never raises for a model failure."""
    import llm

    chat = chat or llm.chat
    lines = facts_text(facts)
    try:
        reply = chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": lines},
            ],
            max_tokens=MAX_TOKENS,
        )
    except llm.ModelOutputError:
        return Overview(None, "no_model")
    except llm.MODEL_ERRORS:
        return Overview(None, "no_model")
    text = " ".join(reply.split())
    if not text:
        return Overview(None, "empty")
    result = check(text, lines)
    if not result.ok:
        return Overview(None, "rejected", result)
    return Overview(text, "shown", result)
