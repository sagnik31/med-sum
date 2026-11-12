"""
The summary's overview: a few sentences the model writes from the
computed facts, and the deterministic check that decides whether they
are shown.

The model sees only `facts_text(facts)`, the facts as plain lines. Its
reply is checked token by token:
- every token with a digit must be a token of the facts. A name made of
  letters and digits ("T4", "Uprise-D3", "60K") is whole: "T4" fails when
  the facts only have a 4. A value ("8.9", "2024-09-05", "1-0-0") must
  also stand near (CONTEXT tokens either side, in the same sentence,
  fillers like "the" or "was" skipped) a word of what the facts say it
  is: code writes the facts, so it knows that 8.9 is TSH's, that
  2025-01-08 is the date of that TSH report, and that 4 is the count of
  documents. So "TSH at 8.9" passes, and a fact's value put beside
  another test, or a date given to a diagnosis it isn't the date of,
  fails;
- every other word must be a word of the facts or one of a fixed list of
  ordinary words (OVERVIEW_WORDS), which holds no test, medicine,
  diagnosis, organ system, severity or judgement.
So a test, medicine or diagnosis that isn't in the facts can't get
through, whatever it is called, and neither can "improving", "worsening",
"abnormal" or a confidence label. A reply that fails is sent back once
with what failed (RETRIES); if the second reply fails too, no overview
is shown: the summary says it was left out and why (counts only), and
the service logs the counts. The check never edits a reply.
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
    hold holds held contain contains
    stated list lists listed recorded printed noted shown shows show include includes
    included including cover covers covered span spans spanning
    date dated dates undated period time times
    based reported reports latest earliest
    rising falling rose fell trend trends
    information provided available given found
    fact facts summary
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

RETRIES = 1  # a rejected reply is sent back once with what failed

# A token: letters and digits, joined by . , ' or - inside it ("8.9",
# "2024-09-05", "Uprise-D3", "TSH's"). One with a digit is a number.
_TOKEN = re.compile(r"[^\W_]+(?:[.,'’\-][^\W_]+)*", re.UNICODE)
# Where an overview sentence ends (a full stop inside "8.9" doesn't).
_SENTENCE_END = re.compile(r"[.!?;]+(?:\s+|$)|\n+")
# Tokens of the facts a number stands near, either side.
CONTEXT = 3
# Words skipped when finding a number's neighbours: they say nothing
# about what the number is.
FILLERS = frozenset(
    "a an the of on in at to and or was were is are be been by for with"
    " from as it its this that".split()
)


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _tokens(text: str) -> list[str]:
    return [re.sub(r"['’]s$", "", t) for t in _TOKEN.findall(_norm(text).casefold())]


def _is_number(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _is_value(token: str) -> bool:
    """A number with no letters: checked in its context, not alone."""
    return not any(c.isalpha() for c in token)


def numbers(text: str) -> set[str]:
    return {t for t in _tokens(text) if _is_number(t)}


def words(text: str) -> set[str]:
    return {t for t in _tokens(text) if not _is_number(t)}


def _known(word: str, vocabulary: set[str]) -> bool:
    if word in vocabulary:
        return True
    # A plural or singular of a known word.
    if word.endswith("s") and word[:-1] in vocabulary:
        return True
    return word + "s" in vocabulary


def _sentences(text: str) -> list[list[str]]:
    return [_tokens(p) for p in _SENTENCE_END.split(text)]


def _values_near(segment: list[str]):
    """Each value of a sentence with the tokens near it, either side."""
    for i, token in enumerate(segment):
        if not (_is_number(token) and _is_value(token)):
            continue
        before = [t for t in segment[:i] if t not in FILLERS][-CONTEXT:]
        after = [t for t in segment[i + 1 :] if t not in FILLERS][:CONTEXT]
        yield token, set(before) | set(after)


class FactLines(str):
    """
    The facts as text (a str, what the model reads), knowing for each value
    in it the words of what it is: `subjects[value]` is one set of words
    per place the value appears.
    """

    subjects: dict[str, list[set[str]]]


class _Writer:
    def __init__(self):
        self.lines: list[str] = []
        self.subjects: dict[str, list[set[str]]] = {}

    def add(self, line: str, *about: tuple[str | None, str]) -> None:
        """A line, and for each (text, subject) the subject of the text's values."""
        self.lines.append(line)
        for text, subject in about:
            if not text:
                continue
            words_of = {t for t in _tokens(subject) if t not in FILLERS}
            for token in _tokens(text):
                if _is_number(token) and _is_value(token):
                    self.subjects.setdefault(token, []).append(words_of - {token})

    def done(self) -> FactLines:
        out = FactLines("\n".join(self.lines))
        out.subjects = self.subjects
        return out


# -------------------------------------------------------------------
# The facts as text
# -------------------------------------------------------------------
def _when(d) -> str:
    return d.date.isoformat() if d.date is not None else "undated"


DOCUMENTS = "documents document records"


def facts_text(facts: Facts) -> FactLines:
    """
    The computed facts as plain lines: what the model reads, and what its
    words are checked against, with the subject of every value in them.
    """
    w = _Writer()
    dated = facts.dated
    kinds: dict[str, int] = {}
    for d in facts.documents:
        kind = d.type.value.replace("_", " ")
        kinds[kind] = kinds.get(kind, 0) + 1
    kind_text = ", ".join(f"{n} {k}" for k, n in kinds.items())
    span = (
        f", dated {dated[0].date.isoformat()} to {dated[-1].date.isoformat()}"
        if dated
        else ""
    )
    w.add(
        f"Documents: {len(facts.documents)} ({kind_text}){span}.",
        (str(len(facts.documents)), DOCUMENTS),
        *((str(n), f"{k} {DOCUMENTS}") for k, n in kinds.items()),
        (span, f"{DOCUMENTS} dated"),
    )
    if facts.undated:
        n = str(len(facts.undated))
        w.add(f"Undated documents: {n}.", (n, f"undated {DOCUMENTS}"))
    if facts.awaiting:
        n = str(facts.awaiting)
        w.add(f"Values awaiting review: {n}.", (n, "values value awaiting review"))

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
        when = _when(f.doc)
        w.add(
            f"Flagged in the latest report: {o.test_name} {o.value_text}{unit}"
            f" ({'; '.join(flag)}), {when}.",
            (o.test_name, o.test_name),  # "Glucose (2 hr)"
            (o.value_text, o.test_name),
            (o.unit, o.test_name),
            (o.flag_printed, o.test_name),
            # A date is near its test's name, or its value and unit.
            (when, f"{o.test_name} {o.value_text}{unit} latest report reports dated"),
        )
    for series in facts.labs:
        if series.trend is not None:
            dates = ", ".join(_when(v.doc) for v in series.trend.values)
            n = str(len(series.trend.values))
            w.add(
                f"Trend: {series.name} {series.trend.direction}, based on "
                f"{n} reports ({dates}).",
                (series.name, series.name),
                (n, f"{series.name} reports report based"),
                (dates, f"{series.name} reports report"),
            )
    rx = facts.latest_prescription
    if rx is not None:
        lines = [(m, " ".join(medicine_line(m.item))) for m in rx.medicines]
        when = _when(rx.doc)
        w.add(
            f"Medicines on the latest prescription ({when}): "
            + "; ".join(line for _, line in lines)
            + ".",
            (when, "latest prescription prescriptions medicines dated"),
            *(
                (line, f"{m.item.printed.form or ''} {m.item.printed.name}")
                for m, line in lines
            ),
        )
    for c in facts.conditions:
        kind = c.item.printed.kind.value.replace("_", " ")
        text, when = c.item.printed.text, _when(c.doc)
        w.add(
            f"Stated {kind}: {text} ({when}).",
            (text, text),  # "Type 2 diabetes mellitus"
            (when, f"{text} {kind} stated"),
        )
    for a in facts.allergies:
        text, when = a.item.printed.text, _when(a.doc)
        w.add(
            f"Allergy recorded: {text} ({when}).",
            (text, text),
            (when, f"{text} allergy allergies recorded"),
        )
    return w.done()


# -------------------------------------------------------------------
# The check
# -------------------------------------------------------------------
@dataclass(frozen=True)
class CheckResult:
    # What failed. Sent back to the model on a retry; only the counts are
    # logged or shown.
    numbers: tuple[str, ...] = ()
    words: tuple[str, ...] = ()

    @property
    def unknown_numbers(self) -> int:
        return len(self.numbers)

    @property
    def unknown_words(self) -> int:
        return len(self.words)

    @property
    def ok(self) -> bool:
        return not self.numbers and not self.words


def check(overview: str, facts_lines: FactLines) -> CheckResult:
    """
    Whether every name with a digit of the overview is one of the facts,
    every value is a value of the facts near a word of what it is there,
    and
    every other word is known.
    """
    if not isinstance(facts_lines, FactLines):
        raise TypeError("check needs the facts as facts_text writes them")
    vocabulary = words(facts_lines) | OVERVIEW_WORDS
    known = facts_lines.subjects
    names = {t for t in numbers(facts_lines) if not _is_value(t)}
    bad_numbers = {t for t in numbers(overview) if not _is_value(t) and t not in names}
    for segment in _sentences(overview):
        for token, near in _values_near(segment):
            if not any(near & there for there in known.get(token, [])):
                bad_numbers.add(token)
    bad_words = {w for w in words(overview) if not _known(w, vocabulary)}
    return CheckResult(tuple(sorted(bad_numbers)), tuple(sorted(bad_words)))


def retry_message(result: CheckResult) -> str:
    """What the model is told when its overview failed the check."""
    parts = []
    if result.numbers:
        parts.append(
            "these numbers are not in the facts, or not beside the same words: "
            + ", ".join(result.numbers)
        )
    if result.words:
        parts.append("these words are not in the facts: " + ", ".join(result.words))
    return (
        "Your overview can't be shown: "
        + "; ".join(parts)
        + ". Write it again using only the facts, with every number next to"
        " what it is in the facts, spelled the same way."
    )


# -------------------------------------------------------------------
# The model call
# -------------------------------------------------------------------
@dataclass(frozen=True)
class Overview:
    text: str | None  # shown only when it passed the check
    outcome: str  # "shown", "rejected", "no_model", "empty"
    check: CheckResult | None = None  # of the last reply checked
    attempts: int = 0  # model replies received


def write_overview(facts: Facts, system_prompt: str, *, chat=None) -> Overview:
    """
    The model's overview of the facts, checked. A reply that fails the
    check is sent back RETRIES times with what failed. Never raises for a
    model failure.
    """
    import llm

    chat = chat or llm.chat
    lines = facts_text(facts)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": lines},
    ]
    result = None
    attempts = 0
    for _ in range(1 + RETRIES):
        try:
            reply = chat(list(messages), max_tokens=MAX_TOKENS)
        except (llm.ModelOutputError, *llm.MODEL_ERRORS):
            if result is not None:  # the retry got no answer
                return Overview(None, "rejected", result, attempts)
            return Overview(None, "no_model")
        attempts += 1
        text = " ".join(reply.split())
        if not text:
            if result is not None:
                return Overview(None, "rejected", result, attempts)
            return Overview(None, "empty", attempts=attempts)
        result = check(text, lines)
        if result.ok:
            return Overview(text, "shown", result, attempts)
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": retry_message(result)},
        ]
    return Overview(None, "rejected", result, attempts)
