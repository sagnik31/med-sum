"""
The pipeline's nodes. Each takes the graph state and returns only the keys
it changes, and each is wrapped in `traced`, so every call is a step of
the current run (section 8.4).

    open        count the pages and describe each page image
    transcribe  one page per call: the vision model's numbered lines, with
                the PDF's own text for that page when it has one; a page
                this document's earlier run read with the same model and
                prompt (same image hash) is used again, with no model call;
                a page cut off by the output cap is read once more in two
                overlapping halves (pipeline/reread.py)
    classify    the document type: the model's label checked against
                keywords (pipeline/classify.py)
    parse       the type's schema filled in from the transcription
    repair      the parse again, told what was wrong with its last reply
    fallback    switch to the fallback model once the primary's tries are used
    validate    the record built by code, and the validators' findings
    render      the document view, built by code from the record
    finish      the low-confidence signals, the record saved, the end state

What each step stores as its input is only what that node reads, with one
exception: the transcription is stored once, as the output of its
transcribe step, and later steps store the page numbers they read.

The order: transcribe comes before classify, so the classifier and its
keyword check read text, and a document of type other still gets its
pages transcribed for the person who reviews it.

Routing between the nodes is the graph's (pipeline/routing.py and
pipeline/graph.py), not theirs. A node raises only for what no retry can
fix (an unreadable file, no model answering); a parse reply that doesn't
fit its schema fails the step without raising, and the graph decides what
comes next.
"""

import hashlib
import json
from datetime import date

from pydantic import ValidationError

import psycopg

import llm
import model_health
from derive import build_record
from observability import add_identifiers, current_step, stamp, traced
from observability.jsonable import jsonable
from pipeline import pages as page_files
from pipeline import parts, reread, signals, store
from pipeline.classify import decide, disagree, keyword_check
from pipeline.prompts import (
    END_MARKER,
    MAX_TOKENS_PER_PAGE,
    PARSE_PROMPTS,
    prompt,
    transcription_messages,
)
from pipeline.parts import ParsePart, PartResult
from pipeline.state import GraphState
from record.document import PageTranscription
from record.printed import PARSE_MODELS, ClassifierOutput, DocumentType
from record.schemas import classifier_schema, parse_output_schema
from validators import Severity
from validators import validate as run_validators
from views.document import document_html

CLASSIFY_MAX_TOKENS = 64
# Characters of transcription the classifier reads: the first page or two
# is plenty to tell the type, and keeps the call short.
CLASSIFY_CHARS = 6000
PARSE_MAX_TOKENS = 8192
# Why a step failed when every model's reply hit its token cap.
CAPPED = "every model's reply hit the token cap"

# The date validator 7 compares against; a function so tests can fix it.
today = date.today


def _page_numbers(state: GraphState) -> list[int]:
    return [p.page for p in state.get("transcriptions", [])]


def _note_expected_patient(state: GraphState) -> None:
    """
    Who the document should be for, noted on the run so stored findings
    and errors mask it (observability.redact).
    """
    expected = state.get("expected_patient")
    dob = getattr(expected, "date_of_birth", None)
    add_identifiers(
        state.get("uploaded_by"),
        getattr(expected, "name", None),
        getattr(expected, "mrn", None),
        *(
            (dob.isoformat(), dob.strftime("%d/%m/%Y"), dob.strftime("%d-%m-%Y"))
            if dob
            else ()
        ),
    )


def _note_printed_patient(parsed) -> None:
    """The patient as the parse read them, noted like the expected one."""
    patient = getattr(parsed, "patient", None)
    if patient is None:
        return
    add_identifiers(*(
        getattr(getattr(patient, name, None), "text", None)
        for name in ("name", "patient_id", "date_of_birth")
    ))  # fmt: skip


def _step():
    step = current_step()
    assert step is not None, "a node runs inside its traced step"
    return step


# -------------------------------------------------------------------
# open
# -------------------------------------------------------------------
@traced("open", input_of=lambda s: {"document_id": s["document_id"]})
def open_document(state: GraphState) -> dict:
    _note_expected_patient(state)
    path = state["file_path"]
    n = page_files.page_count(path)
    images = [
        page_files.describe(i, page_files.page_png(path, i)) for i in range(1, n + 1)
    ]
    return {"pages_in_file": n, "pages": images}


# -------------------------------------------------------------------
# transcribe
# -------------------------------------------------------------------
def next_page(state: GraphState) -> int | None:
    """The first page not transcribed yet, or None when all are."""
    done = set(_page_numbers(state))
    return next((p.page for p in state.get("pages", []) if p.page not in done), None)


def _transcribe_input(state: GraphState) -> dict:
    n = next_page(state)
    return {"page": n, "image": state["pages"][n - 1] if n else None}


def lines_of(reply: str) -> list[str]:
    """The model's Markdown as numbered lines: one per line, blank ones kept."""
    return reply.split("\n") if reply.strip() else []


def _earlier_transcription(state: GraphState, n: int, step):
    """An earlier run's lines for page n, or None (also when the lookup fails)."""
    try:
        return store.earlier_transcription(
            state["document_id"],
            image_sha256=state["pages"][n - 1].sha256,
            prompt_hash=step.prompt_hash,
            model=llm.PRIMARY_MODEL,
            run_id=step.run.id,
        )
    except psycopg.Error:
        return None  # the page is read again


@traced("transcribe", page=next_page, input_of=_transcribe_input)
def transcribe(state: GraphState) -> dict:
    n = next_page(state)
    if n is None:
        raise ValueError("every page is already transcribed")
    path = state["file_path"]
    image = page_files.page_png(path, n)
    if page_files.describe(n, image) != state["pages"][n - 1]:
        raise page_files.DocumentFileError(f"page {n} changed since it was counted")

    step = _step()
    step.set_prompt("extract_page", prompt("extract_page"))
    text_layer = page_files.text_layer(path, n)
    earlier = _earlier_transcription(state, n, step)
    if earlier is not None:
        # The same pixels, read by the same model with the same prompt in
        # an earlier run: its lines are used again, with no model call.
        step_id, lines = earlier
        step.reuse(step_id, model=llm.PRIMARY_MODEL, role="primary")
        page = PageTranscription(page=n, lines=lines, text_layer=text_layer)
        step.set_output({"transcriptions": [page], "reused_from_step": step_id})
        return {"transcriptions": [page]}
    calls_before = len(step.model_call_list())
    note = None
    try:
        lines = lines_of(_read_page(image))
    except llm.OutputCappedError:
        lines, note = _reread_in_halves(image)
        if lines is None:
            step.fail(f"{CAPPED}; read again in halves: {note['reason']}")
    if lines is None:
        # The page is longer than a call can hold, even in halves (or the
        # halves didn't join). It stays in the record with no lines, so
        # its content can't be cited, and the document goes to review
        # (signal LC-02) rather than failing outright.
        page = PageTranscription(page=n, lines=[], text_layer=text_layer)
        out = {
            "transcriptions": [page],
            "capped_pages": [*state.get("capped_pages", []), n],
        }
    else:
        page = PageTranscription(page=n, lines=lines, text_layer=text_layer)
        out = {"transcriptions": [page]}
    if note is not None:  # the step's output says how the re-read went
        step.set_output({"transcriptions": [page], "reread": note})
    return {**out, **_truncated_page(state, step, calls_before, n)}


def _read_page(image: bytes) -> str:
    """One transcription call on a page image (or part of one)."""
    return llm.chat(
        transcription_messages(image, prompt("extract_page")),
        max_tokens=MAX_TOKENS_PER_PAGE,
        stop_marker=END_MARKER,
    )


def _reread_in_halves(image: bytes) -> tuple[list[str] | None, dict]:
    """
    The lines of a page whose reading hit the output cap, read again as
    two overlapping halves and joined (None when a half is capped too or
    the halves don't join), and a note of what happened for the step's
    output. Every call is recorded on the step as usual.
    """
    halves = []
    for top, bottom in reread.HALVES:
        try:
            halves.append(
                lines_of(_read_page(page_files.crop_band(image, top, bottom)))
            )
        except llm.OutputCappedError:
            return None, _note(
                "not joined", halves, reason="a half hit the token cap too"
            )
    joined = reread.join_halves(*halves)
    if joined is None:
        return None, _note("not joined", halves,
                           reason="the halves share no single run of lines")  # fmt: skip
    lines, overlap = joined
    return lines, _note("joined", halves, overlap_lines=overlap)


def _note(outcome: str, halves: list[list[str]], **more) -> dict:
    return {"outcome": outcome, "half_lines": [len(h) for h in halves], **more}


def _truncated_page(state: GraphState, step, calls_before: int, n: int) -> dict:
    """The page added to truncated_pages when its call's prompt may have been cut."""
    if not _truncated(step, calls_before):
        return {}
    return {"truncated_pages": [*state.get("truncated_pages", []), n]}


# -------------------------------------------------------------------
# classify
# -------------------------------------------------------------------
def plain_text(transcriptions: list[PageTranscription]) -> str:
    return "\n\n".join("\n".join(p.lines) for p in transcriptions)


@traced("classify", input_of=lambda s: {"pages": _page_numbers(s)})
def classify(state: GraphState) -> dict:
    text = plain_text(state["transcriptions"])
    keywords = keyword_check(text)

    step = _step()
    step.set_prompt("classify_document", prompt("classify_document"))
    label, model_error = None, None
    try:
        reply = llm.chat(
            [
                {"role": "system", "content": prompt("classify_document")},
                {"role": "user", "content": text[:CLASSIFY_CHARS]},
            ],
            max_tokens=CLASSIFY_MAX_TOKENS,
            format=classifier_schema(),
        )
        label = DocumentType(ClassifierOutput.model_validate_json(reply).document_type)
    except (ValidationError, llm.ModelOutputError) as e:
        # The keyword check stands in; an unusable label is not a failure.
        model_error = type(e).__name__

    decision = decide(label, keywords.type)
    step.set_output(
        {
            "doc_type": decision.type,
            "rule": decision.rule,
            "model_label": label,
            "model_error": model_error,
            "keyword_type": keywords.type,
            "lab_signals": keywords.lab_signals,
            "prescription_signals": keywords.prescription_signals,
        }
    )
    return {
        "doc_type": decision.type,
        "classify_disagree": disagree(label, keywords.type),
    }


# -------------------------------------------------------------------
# parse
# -------------------------------------------------------------------
def parse_parts(state: GraphState) -> list[ParsePart]:
    """The parts the document is parsed in (planned in code from its pages)."""
    return parts.plan(state["transcriptions"], state["doc_type"])


def next_part(state: GraphState) -> ParsePart | None:
    """The first part without a reply in this round, or None when all have one."""
    done = {r.index for r in state.get("part_results", [])}
    return next((p for p in parse_parts(state) if p.index not in done), None)


def _round(state: GraphState) -> int:
    """The parse attempt a parse step belongs to: a new one at the first part."""
    attempts = state.get("parse_attempts", 0)
    return attempts if state.get("part_results") else attempts + 1


def _parse_input(state: GraphState) -> dict:
    part = next_part(state)
    return {
        "doc_type": state.get("doc_type"),
        "model_role": state.get("model_role", "primary"),
        "attempt": _round(state),
        "pages": _page_numbers(state),
        "part": part.model_dump(mode="json") if part else None,
        "parts": len(parse_parts(state)),
    }


def _store_answered(answered: list[PartResult], out: dict) -> None:
    """
    What a parse or repair step stores as its output: the replies of the
    parts it answered (not the earlier parts' again), and whether the
    parts were merged. A refused part fails the step, without raising.
    """
    step = _step()
    step.set_output({
        "part_results": [
            r.model_dump(mode="json", exclude={"provenance"}) for r in answered
        ],
        "merged": out.get("parse") is not None if "parse_error" in out else None,
    })  # fmt: skip
    errors = [r.error for r in answered if r.parse is None and r.error]
    if errors:
        step.fail("\n".join(errors))


def schema_errors(error: ValidationError) -> str:
    """Where and why a reply broke the schema, one line each, for the repair prompt."""
    lines = []
    for e in error.errors(include_url=False, include_input=False):
        where = ".".join(str(part) for part in e["loc"]) or "(the reply)"
        lines.append(f"{where}: {e['msg']}")
    return "\n".join(lines)


def _part_call(state: GraphState, part: ParsePart, messages: list[dict]) -> PartResult:
    """
    One parse call for one part with the model in use, the reply checked
    against the type's model. A reply that is cut off, not JSON or off the
    schema is a refused part (its error says why), not an exception; no
    model answering raises.
    """
    step = _step()
    doc_type = state["doc_type"]
    calls_before = len(step.model_call_list())
    try:
        reply = llm.chat(
            messages,
            max_tokens=PARSE_MAX_TOKENS,
            format=parse_output_schema(doc_type),
            model_role=state.get("model_role", "primary"),
        )
    except llm.OutputCappedError:
        # A cut-off reply is a part that failed, like one off the schema.
        model_health.quality_failure(step.model, step.model_role, "capped")
        return PartResult(
            index=part.index, error=CAPPED, model_role=step.model_role,
            truncated=_truncated(step, calls_before),
        )  # fmt: skip
    try:
        parsed = PARSE_MODELS[doc_type].model_validate_json(reply)
    except ValidationError as e:
        model_health.quality_failure(step.model, step.model_role, "off_schema")
        return PartResult(
            index=part.index, reply=reply, error=schema_errors(e),
            model_role=step.model_role, truncated=_truncated(step, calls_before),
        )  # fmt: skip
    _note_printed_patient(parsed)
    return PartResult(
        index=part.index,
        reply=reply,
        parse=parsed,
        # What produced this part's parse, for its items in the record.
        provenance=step.provenance(),
        model_role=step.model_role,
        truncated=_truncated(step, calls_before),
    )


def _truncated(step, calls_before: int) -> bool:
    """Whether a call since `calls_before` may have had its prompt cut."""
    return any(c.prompt_truncated for c in step.model_call_list()[calls_before:])


def _combined(state: GraphState, results: list[PartResult]) -> dict:
    """
    The state once every part has a reply: the merged parse when all fit,
    else no parse and why the refused parts were refused. A failed round
    leaves the last merged parse's provenance in place, since the record
    validate keeps is built from that parse.
    """
    plan = {p.index: p for p in parse_parts(state)}
    refused = [r for r in results if r.parse is None]
    if refused:
        # With several parts, each line says which part it is about.
        where = (
            (lambda r: f"{_where(plan[r.index])}: ")
            if len(plan) > 1
            else (lambda r: "")
        )
        return {
            "parse": None,
            "parse_error": "\n".join(
                f"{where(r)}{line}"
                for r in refused
                for line in (r.error or "").splitlines()
                if line.strip()
            ),
        }
    roles = {r.model_role for r in results}
    return {
        "parse": parts.merge([r.parse for r in results]),
        "parse_error": None,
        "parse_provenance": results[0].provenance,
        "part_provenance": [r.provenance for r in results],
        "parse_model_role": "fallback" if "fallback" in roles else "primary",
        "parse_truncated": any(r.truncated for r in results),
    }


def _where(part: ParsePart) -> str:
    return f"page {part.page} lines {part.first_line}-{part.last_line}"


def _parse_prompt(state: GraphState) -> str:
    doc_type = state["doc_type"]
    if doc_type not in PARSE_MODELS:
        raise ValueError(f"documents of type {doc_type.value} are not parsed")
    return PARSE_PROMPTS[doc_type]


def _part_messages(state: GraphState, name: str, part: ParsePart) -> list[dict]:
    return [
        {"role": "system", "content": prompt(name)},
        {"role": "user", "content": parts.part_text(state["transcriptions"], part)},
    ]


@traced(
    "parse",
    page=lambda s: (part := next_part(s)) and part.page,
    attempt=_round,
    input_of=_parse_input,
)
def parse(state: GraphState) -> dict:
    """
    One part of the document parsed (one part per visit, so a run that
    stops resumes at the next part). The last part of a round merges the
    parts' parses.
    """
    part = next_part(state)
    if part is None:
        raise ValueError("every part is already parsed")
    name = _parse_prompt(state)
    _step().set_prompt(name, prompt(name))
    result = _part_call(state, part, _part_messages(state, name, part))
    results = [*state.get("part_results", []), result]
    out = {"part_results": results, "parse_attempts": _round(state)}
    if part.index == 0:
        out["parse"] = None  # a new round: no merged parse until it ends
    if len(results) == len(parse_parts(state)):
        out.update(_combined(state, results))
    _store_answered([result], out)
    return out


# -------------------------------------------------------------------
# repair
# -------------------------------------------------------------------
def _finding_line(f) -> str:
    where = [f.item_id, f.field]
    if f.page is not None:
        where.append(f"page {f.page}")
    if f.lines:
        where.append("line " + ", ".join(map(str, f.lines)))
    return f"- {' '.join(w for w in where if w)}: {f.message}"


def _parts_of_finding(f, plan: list[ParsePart]) -> list[ParsePart]:
    """The parts a finding is about: the one holding its line, else its page's, else all."""
    held = [
        p
        for p in plan
        if f.page is not None and any(p.holds(f.page, n) for n in f.lines)
    ]
    on_page = [p for p in plan if p.page == f.page]
    return held or on_page or plan


def part_problems(state: GraphState) -> dict[int, list[str]]:
    """
    What was wrong with each part's last reply, one line each, for the
    repair prompt: why it was refused, or else the validators' error
    findings on its lines (flags and info are not the parse's to fix).
    Parts with nothing wrong are left out.
    """
    found: dict[int, list[str]] = {}
    if state.get("parse") is None:
        for r in state.get("part_results", []):
            if r.parse is None:
                found[r.index] = [
                    f"- {line}" for line in (r.error or "").splitlines() if line.strip()
                ]
        return found
    plan = parse_parts(state)
    for f in state.get("findings", []):
        if f.severity is not Severity.ERROR:
            continue
        for part in _parts_of_finding(f, plan):
            found.setdefault(part.index, []).append(_finding_line(f))
    return found


def problems(state: GraphState) -> list[str]:
    """Every part's problems, in part order."""
    found = part_problems(state)
    return [line for index in sorted(found) for line in found[index]]


def _repair_input(state: GraphState) -> dict:
    found = part_problems(state)
    return {
        "doc_type": state.get("doc_type"),
        "model_role": state.get("model_role", "primary"),
        "attempt": state.get("parse_attempts", 0) + 1,
        "pages": _page_numbers(state),
        "parts": sorted(found),
        "problems": sum(len(v) for v in found.values()),
    }


@traced("repair", input_of=_repair_input)
def repair(state: GraphState) -> dict:
    """
    The parts with problems parsed again, each with its previous reply and
    what was wrong with it in the conversation, so the model corrects its
    own reply rather than starting over. Parts without problems keep their
    parse. The validators judge the merged result like any parse.
    """
    name = _parse_prompt(state)
    found = part_problems(state)
    if not found:
        raise ValueError("there is nothing to repair")
    instructions = prompt("repair_parse")
    _step().set_prompt(f"{name}+repair_parse", prompt(name) + instructions)
    plan = {p.index: p for p in parse_parts(state)}
    by_index = {r.index: r for r in state.get("part_results", [])}
    answered = []
    for index in sorted(found):
        previous = by_index.get(index)
        messages = _part_messages(state, name, plan[index])
        reply = previous.reply if previous else None
        if reply is None and previous is not None and previous.parse is not None:
            reply = previous.parse.model_dump_json()
        if reply:
            messages.append({"role": "assistant", "content": reply})
        messages.append(
            {"role": "user", "content": instructions + "\n".join(found[index])}
        )
        by_index[index] = _part_call(state, plan[index], messages)
        answered.append(by_index[index])
    results = [by_index[i] for i in sorted(by_index)]
    out = {
        "part_results": results,
        "parse_attempts": state.get("parse_attempts", 0) + 1,
        "repairs": state.get("repairs", 0) + 1,
        **_combined(state, results),
    }
    _store_answered(answered, out)
    return out


# -------------------------------------------------------------------
# fallback
# -------------------------------------------------------------------
@traced("fallback", input_of=lambda s: {"attempts": s.get("parse_attempts", 0)})
def use_fallback(state: GraphState) -> dict:
    """
    The primary model's attempts are used up: parse every part again from
    the start with the fallback model, with a budget of its own.
    """
    return {"model_role": "fallback", "parse_attempts": 0, "part_results": []}


# -------------------------------------------------------------------
# validate
# -------------------------------------------------------------------
def content_ref(value) -> dict | None:
    """
    What a step stores in place of content another store already holds:
    its SHA-256 and size. See docs/adr/0037-data-classes-and-redaction.md.
    """
    if value is None:
        return None
    text = (
        value
        if isinstance(value, str)
        else json.dumps(jsonable(value), sort_keys=True, ensure_ascii=False)
    )
    data = text.encode("utf-8")
    return {"sha256": hashlib.sha256(data).hexdigest(), "chars": len(text)}


def _validate_input(state: GraphState) -> dict:
    # The parse is in the parse steps' replies, the patient in the account:
    # references only.
    return {
        "doc_type": state.get("doc_type"),
        "parse": content_ref(state.get("parse")),
        "expected_patient": state.get("expected_patient") is not None,
        "pages": _page_numbers(state),
    }


def _validate_output(result: dict) -> dict:
    # The record is in document_records, the findings in their own column.
    record = result["record"]
    return {
        "record": content_ref(record.model_dump(mode="json", exclude={"pages"})),
        "items": sum(1 for _ in record.items()),
    }


@traced("validate", input_of=_validate_input, output_of=_validate_output)
def validate(state: GraphState) -> dict:
    doc_type = state["doc_type"]
    parse_result = state.get("parse") if doc_type in PARSE_MODELS else None
    gave_up = parse_result is None and state.get("parse_error") is not None
    if doc_type in PARSE_MODELS and parse_result is None and not gave_up:
        raise ValueError("there is no parse to validate")
    # A parsed type without a parse is one whose every attempt failed: its
    # record holds the pages alone, for review (signal LC-08).
    transcriptions = state["transcriptions"]
    record = build_record(
        document_id=state["document_id"],
        doc_type=doc_type,
        pages=transcriptions,
        has_text_layer=any(p.text_layer for p in transcriptions),
        parse=parse_result,
        uploaded_by=state.get("uploaded_by"),
    )
    # Pages alone have nothing extracted to check.
    findings = (
        run_validators(
            record, expected_patient=state.get("expected_patient"), today=today()
        )
        if parse_result is not None
        else []
    )
    _step().add_findings(findings)
    if any(f.severity is Severity.ERROR for f in findings):
        made_by = state.get("parse_provenance")
        model_health.quality_failure(
            made_by.model if made_by else None, state.get("parse_model_role"),
            "validation",
        )  # fmt: skip
    return {
        "record": record,
        "findings": findings,
        "record_parsed": parse_result is not None,
    }


# -------------------------------------------------------------------
# The record marked with its signals (render and finish)
# -------------------------------------------------------------------
def _breaker_switches() -> list[dict]:
    step = current_step()
    return list(step.run.breaker_switches) if step is not None else []


def marked_record(state: GraphState, provenance) -> tuple:
    """
    A copy of the state's record with provenance on every item and the
    section 8.8 signals applied (low confidence with reasons, or validated),
    the signals, and whether the record was parsed. `provenance` gives the
    provenance for a record that wasn't parsed (called only then). Apart
    from that call it reads only the state and the run's breaker switches
    (OP-3), and no model is called between render and finish, so render
    shows exactly what finish saves.
    """
    record = state.get("record")
    if record is None:
        raise ValueError("there is no record to finish")
    record = record.model_copy(deep=True)
    parsed = state.get("record_parsed", False)
    hits = signals.collect(
        doc_type=state.get("doc_type"),
        record=record,
        parsed=parsed,
        findings=state.get("findings", []),
        capped_pages=state.get("capped_pages", []),
        truncated_pages=state.get("truncated_pages", []),
        parse_truncated=parsed and state.get("parse_truncated", False),
        classify_disagree=state.get("classify_disagree", False),
        record_model_role=state.get("parse_model_role") if parsed else None,
        repairs=state.get("repairs", 0),
        breaker_switches=_breaker_switches(),
    )
    if parsed:
        _stamp_by_part(record, state)
        stamp(record, state["parse_provenance"])
    else:
        stamp(record, provenance())
    signals.apply(record, hits)
    return record, hits, parsed


def _stamp_by_part(record, state: GraphState) -> None:
    """Each item gets the provenance of the part whose lines it cites."""
    plan = parse_parts(state)
    made_by = state.get("part_provenance") or []
    for item in record.items():
        c = item.printed.citation
        for part in plan:
            if part.holds(c.page, c.first_line) and part.index < len(made_by):
                if made_by[part.index] is not None:
                    item.provenance = made_by[part.index].model_copy(deep=True)
                break


# -------------------------------------------------------------------
# render
# -------------------------------------------------------------------
def _render_input(state: GraphState) -> dict:
    return {
        "pages": _page_numbers(state),
        "record_parsed": state.get("record_parsed", False),
        "findings": len(state.get("findings", [])),
    }


def _render_output(result: dict) -> dict:
    # The page itself is stored in insights.
    return {"insight_html": content_ref(result["insight_html"])}


@traced("render", input_of=_render_input, output_of=_render_output)
def render(state: GraphState) -> dict:
    """
    The document view, built by code from the record (views/document.py),
    with the same review marks the finish node gives it. No model call.
    """
    record, _, parsed = marked_record(state, lambda: _step().provenance())
    return {"insight_html": document_html(record, parsed=parsed)}


# -------------------------------------------------------------------
# finish
# -------------------------------------------------------------------
def _finish_input(state: GraphState) -> dict:
    return {
        "doc_type": state.get("doc_type"),
        "record_parsed": state.get("record_parsed", False),
        "findings": len(state.get("findings", [])),
        "capped_pages": state.get("capped_pages", []),
        "repairs": state.get("repairs", 0),
    }


def _finish_output(result: dict) -> dict:
    return {
        "end_state": result["end_state"],
        "signals": [h.model_dump(mode="json") for h in result["signals"]],
    }


@traced("finish", input_of=_finish_input, output_of=_finish_output)
def finish(state: GraphState) -> dict:
    """
    The document's signals (section 8.8), its record marked with them and
    saved, and its end state: needs_review when any signal routes, else
    verified. Every item must trace back to the step that produced it; a
    gap is a bug, so the record is not saved and the run fails.
    """
    step = _step()
    record, hits, _ = marked_record(state, step.provenance)
    store.save_record(record, run_id=step.run.id)
    end_state = "needs_review" if signals.needs_review(hits) else "verified"
    return {"record": record, "signals": hits, "end_state": end_state}


# Every node, by the name the graph gives it.
NODES = {
    "open": open_document,
    "transcribe": transcribe,
    "classify": classify,
    "parse": parse,
    "repair": repair,
    "fallback": use_fallback,
    "validate": validate,
    "render": render,
    "finish": finish,
}
