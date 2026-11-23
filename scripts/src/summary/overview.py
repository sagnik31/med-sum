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
- a word that claims something (CLAIMS: rising, falling, high, low,
  normal, flagged) must stand near what the facts say it of, and a
  negation (no, not, none) right next to a kind of fact the facts list
  none of ("Trends: none."), so "TSH is rising", "1.6 is normal" or "no
  allergies" fail when the facts say otherwise; a number written as a
  word ("five") is checked as that number;
- a word of printed free text (a diagnosis, an allergy, a medicine's
  instructions) must stand beside another word of that text, so a word
  printed in one place ("improving" in a diagnosis) can't be said of
  something else;
- every other word must be a fixed word of the facts' own lines, a test,
  medicine or unit name, or one of a fixed list of ordinary words
  (OVERVIEW_WORDS), which holds no test, medicine, diagnosis, organ
  system, severity, judgement, negation or direction.
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
    only just still again
    is are was were be been being has have had do does did
    of in on at to from by for with over under between across
    after before during since until through per about into than then
    as such same other another more most less fewer first last latest earlier
    later previous recent next
    patient patients document documents report reports record records
    result results value values test tests reading readings
    lab labs laboratory prescription prescriptions prescribed medicine medicines
    hold holds held contain contains
    stated list lists listed recorded printed noted shown shows show include includes
    included including cover covers covered span spans spanning
    date dated dates undated period time times
    based reported reports latest earliest
    trend trends
    information provided available given found
    fact facts summary
    range ranges reference
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

# Words that claim something about a value or a list, so they are true
# only beside what the facts say it of, like a number: "falling" only
# within CONTEXT tokens of a test whose trend falls, "high" or "flagged"
# beside a value flagged so, "no" or "none" right next to a kind of fact
# the facts list none of ("No prescriptions": the facts say
# "Prescriptions: none"). Anywhere else they would contradict the facts
# with words the check otherwise knows ("TSH is rising", "1.6 is within
# range", "no allergies" when one is recorded), so they are checked by
# place even when another fact happens to contain them.
# See docs/adr/0007-pages-built-by-code.md.
CLAIMS = frozenset(
    "rising falling rose fell rises falls increasing decreasing"
    " high low normal flag flags flagged".split()
)
NEGATIONS = frozenset("no not none nil neither nor never without".split())
# A number written as a word is checked as that number: "Five documents"
# is the number 5, beside "documents".
NUMBER_WORDS = {
    w: str(n)
    for n, w in enumerate(
        "zero one two three four five six seven eight nine ten".split()
    )
}

RETRIES = 1  # a rejected reply is sent back once with what failed

# A token: letters and digits, joined by . , ' or - inside it ("8.9",
# "2024-09-05", "Uprise-D3", "TSH's"). One with a digit is a number.
_TOKEN = re.compile(r"[^\W_]+(?:[.,'’\-][^\W_]+)*", re.UNICODE)
# Where an overview sentence ends (a full stop inside "8.9" doesn't).
_SENTENCE_END = re.compile(r"[.!?;]+(?:\s+|$)|\n+")
# Tokens of the facts a number stands near, either side.
CONTEXT = 3
# The same for a claim word (CLAIMS).
CLAIM_CONTEXT = 6
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
    The facts as text (a str, what the model reads), knowing what each
    word or value in it may stand beside:
    - `subjects[value]`: the words of what the value is, one set per place;
    - `claims[word]`: for a claim word (CLAIMS), what it is said of;
    - `absent`: the words for kinds of fact the facts have none of, which
      a negation may stand right next to;
    - `phrases[word]`: for a word of printed free text (a diagnosis, an
      allergy, a medicine's instructions), the other words of that text;
      it may be used only beside one of them;
    - `vocabulary`: every other word of the facts (fixed wording, test,
      medicine and unit names), usable anywhere.
    """

    subjects: dict[str, list[set[str]]]
    claims: dict[str, list[set[str]]]
    absent: set[str]
    phrases: dict[str, list[set[str]]]
    vocabulary: set[str]


def _subject_words(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in FILLERS}


class _Writer:
    def __init__(self):
        self.lines: list[str] = []
        self.subjects: dict[str, list[set[str]]] = {}
        self.claims: dict[str, list[set[str]]] = {}
        self.absent: set[str] = set()
        self.phrases: dict[str, list[set[str]]] = {}
        self.vocabulary: set[str] = set()

    def add(
        self,
        line: str,
        *about: tuple[str | None, str],
        free: tuple[str | None, ...] = (),
        free_beside: str = "",
        claims: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """
        A line; for each (text, subject) the subject of the text's values;
        the printed free texts in it, whose words may stand beside each
        other or the words of `free_beside`; and each (claim words, what
        they are said of).
        """
        self.lines.append(line)
        for text, subject in about:
            if not text:
                continue
            words_of = _subject_words(subject)
            for token in _tokens(text):
                if _is_number(token) and _is_value(token):
                    self.subjects.setdefault(token, []).append(words_of - {token})
        rest = line
        for text in free:
            if not text:
                continue
            rest = rest.replace(text, " ")
            phrase = words(text)
            for word in phrase:
                partners = (phrase | _subject_words(free_beside)) - {word}
                if partners:
                    self.phrases.setdefault(word, []).append(partners)
                else:  # a one-word text ("Penicillin") stands alone
                    self.vocabulary.add(word)
        self.vocabulary |= words(rest)
        for claim_words, subject in claims:
            for w in _tokens(claim_words):
                self.claims.setdefault(w, []).append(_subject_words(subject) - {w})

    def none_of(self, line: str, kind: str) -> None:
        """A line saying the facts hold nothing of a kind, which "no" may be said of."""
        self.lines.append(line)
        self.vocabulary |= words(line) - NEGATIONS
        self.absent |= _subject_words(kind)

    def done(self) -> FactLines:
        out = FactLines("\n".join(self.lines))
        out.subjects = self.subjects
        out.claims = self.claims
        out.absent = self.absent
        out.phrases = self.phrases
        out.vocabulary = self.vocabulary
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

    # The latest value of each flagged test, with its own date: it can be on
    # an earlier report than the latest one, so the line never says "in the
    # latest report".
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
        said_of = f"{o.test_name} {o.value_text}"
        direction = f.item.derived.flag_computed
        w.add(
            f"Latest flagged value: {o.test_name} {o.value_text}{unit}"
            f" ({'; '.join(flag)}), {when}.",
            (o.test_name, o.test_name),  # "Glucose (2 hr)"
            (o.value_text, o.test_name),
            (o.unit, o.test_name),
            (o.flag_printed, o.test_name),
            # A date is near its test's name, or its value and unit.
            (
                when,
                f"{o.test_name} {o.value_text}{unit}"
                " latest value values report reports dated",
            ),
            claims=(
                ("flag flags flagged", said_of),
                *(((direction.value, said_of),) if direction is not None else ()),
                *_printed_direction(o.flag_printed, said_of),
            ),
        )
    if not facts.attention:
        w.none_of("Latest flagged values: none.", "flagged flags values value")
    for series in facts.labs:
        if series.trend is not None:
            dates = ", ".join(_when(v.doc) for v in series.trend.values)
            n = str(len(series.trend.values))
            direction = series.trend.direction
            w.add(
                f"Trend: {series.name} {direction}, based on {n} reports ({dates}).",
                (series.name, series.name),
                (n, f"{series.name} reports report based"),
                (dates, f"{series.name} reports report"),
                claims=((_DIRECTION_WORDS[direction], series.name),),
            )
    if not any(s.trend is not None for s in facts.labs):
        w.none_of("Trends: none.", "trends trend")
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
            free=tuple(_free_parts(m.item) for m, _ in lines),
        )
    else:
        w.none_of("Prescriptions: none.", "prescriptions prescription medicines")
    for c in facts.conditions:
        kind = c.item.printed.kind.value.replace("_", " ")
        text, when = c.item.printed.text, _when(c.doc)
        w.add(
            f"Stated {kind}: {text} ({when}).",
            (text, text),  # "Type 2 diabetes mellitus"
            (when, f"{text} {kind} stated"),
            free=(text,),
        )
    if not facts.conditions:
        w.none_of("Stated diagnoses: none.", "diagnoses diagnosis conditions")
    for a in facts.allergies:
        text, when = a.item.printed.text, _when(a.doc)
        w.add(
            f"Allergy recorded: {text} ({when}).",
            (text, text),
            (when, f"{text} allergy allergies recorded"),
            free=(text,),
            free_beside="allergy allergies allergic",
        )
    if not facts.allergies:
        w.none_of("Allergies recorded: none.", "allergies allergy")
    return w.done()


_DIRECTION_WORDS = {
    "rising": "rising rose rises increasing",
    "falling": "falling fell falls decreasing",
}
_PRINTED_FLAG_WORDS = {"h": "high", "high": "high", "l": "low", "low": "low"}


def _printed_direction(flag: str | None, said_of: str) -> tuple:
    """A printed H or L, which the overview may call high or low."""
    word = _PRINTED_FLAG_WORDS.get((flag or "").strip().casefold())
    return ((word, said_of),) if word else ()


def _free_parts(m) -> str:
    """The printed parts of a medicine line that are free text, not its name."""
    p = m.printed
    return " ".join(
        v for v in (p.dose, p.frequency, p.route, p.duration, p.instructions) if v
    )


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
    vocabulary = facts_lines.vocabulary | OVERVIEW_WORDS
    known = facts_lines.subjects
    names = {t for t in numbers(facts_lines) if not _is_value(t)}
    bad_numbers, bad_words = set(), set()
    for raw in _sentences(overview):
        segment = [NUMBER_WORDS.get(t, t) for t in raw]
        for token in segment:
            if _is_number(token) and not _is_value(token) and token not in names:
                bad_numbers.add(token)
        for token, near in _values_near(segment):
            if not any(near & there for there in known.get(token, [])):
                bad_numbers.add(token)
        for i, token in enumerate(segment):
            if not _is_number(token) and not _word_ok(
                segment, i, facts_lines, vocabulary
            ):
                bad_words.add(token)
    return CheckResult(tuple(sorted(bad_numbers)), tuple(sorted(bad_words)))


def _near(segment: list[str], i: int, span: int = CONTEXT) -> tuple[list, list]:
    """The non-filler tokens before and after position i, nearest first."""
    before = [t for t in segment[:i] if t not in FILLERS][-span:][::-1]
    after = [t for t in segment[i + 1 :] if t not in FILLERS][:span]
    return before, after


def _forms(word: str) -> set[str]:
    """A word and its plural or singular."""
    return {word, word[:-1] if word.endswith("s") else word + "s"}


def _beside(near: set[str], subjects: list[set[str]]) -> bool:
    return any(near & {f for w in s for f in _forms(w)} for s in subjects)


def _word_ok(
    segment: list[str], i: int, lines: FactLines, vocabulary: set[str]
) -> bool:
    """Whether the word at position i of a sentence may stand there."""
    word = segment[i]
    before, after = _near(segment, i)
    near = set(before) | set(after)
    phrase = _beside(near, lines.phrases.get(word, []))
    if word in NEGATIONS:
        # Right next to a kind of fact the facts have none of, or inside a
        # printed phrase that holds it ("No known allergies").
        nxt = set(after[:1] + (before[:1] if word == "none" else []))
        absent = {f for w in lines.absent for f in _forms(w)}
        return bool(nxt & absent) or phrase
    if word in CLAIMS:
        # A claim often ends a list ("flags Hb 11.9 g/dL, MCV 81 fL as
        # low"), so it may sit further from its value, in the same sentence.
        wide = set().union(*_near(segment, i, CLAIM_CONTEXT))
        return _beside(wide, lines.claims.get(word, [])) or phrase
    return _known(word, vocabulary) or phrase


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
