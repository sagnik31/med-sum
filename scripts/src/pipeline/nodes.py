"""
The pipeline's nodes. Each takes the graph state and returns only the keys
it changes, and each is wrapped in `traced`, so every call is a step of
the current run (section 8.4).

    open        count the pages and describe each page image
    transcribe  one page per call: the vision model's numbered lines, with
                the PDF's own text for that page when it has one
    classify    the document type: the model's label checked against
                keywords (pipeline/classify.py)
    parse       the type's schema filled in from the transcription
    validate    the record built by code, and the validators' findings
    render      the insight page the app shows today

What each step stores as its input is only what that node reads, with one
exception: the transcription is stored once, as the output of its
transcribe step, and later steps store the page numbers they read.

The order: transcribe comes before classify, so the classifier and its
keyword check read text, and a document of type other still gets its
pages transcribed for the person who reviews it.

Routing between the nodes (repair, fallback, end states) is the graph's
(pipeline/graph.py), not theirs. A node raises only for what no retry can
fix (an unreadable file, no model answering); a parse reply that doesn't
fit its schema fails the step without raising, and the graph decides what
comes next.
"""

from datetime import date

from pydantic import ValidationError

import llm
from derive import build_record
from extract_report_slm import END_MARKER, MAX_TOKENS_PER_PAGE, transcription_messages
from observability import current_step, traced
from pipeline import pages as page_files
from pipeline.classify import decide, disagree, keyword_check
from pipeline.prompts import PARSE_PROMPTS, prompt
from pipeline.state import GraphState
from record.document import PageTranscription, numbered_document
from record.printed import PARSE_MODELS, ClassifierOutput, DocumentType
from record.schemas import classifier_schema, parse_output_schema
from validators import validate as run_validators

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


def _step():
    step = current_step()
    assert step is not None, "a node runs inside its traced step"
    return step


# -------------------------------------------------------------------
# open
# -------------------------------------------------------------------
@traced("open", input_of=lambda s: {"document_id": s["document_id"]})
def open_document(state: GraphState) -> dict:
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
    try:
        reply = llm.chat(
            transcription_messages(image, prompt("extract_page")),
            max_tokens=MAX_TOKENS_PER_PAGE,
            stop_marker=END_MARKER,
        )
    except llm.OutputCappedError:
        # The page is longer than a call can hold. It stays in the record
        # with no lines, so its content can't be cited, and the document
        # goes to review (signal LC-02) rather than failing outright.
        step.fail(CAPPED)
        page = PageTranscription(page=n, lines=[], text_layer=text_layer)
        return {
            "transcriptions": [page],
            "capped_pages": [*state.get("capped_pages", []), n],
        }
    page = PageTranscription(page=n, lines=lines_of(reply), text_layer=text_layer)
    return {"transcriptions": [page]}


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
def _parse_input(state: GraphState) -> dict:
    return {
        "doc_type": state.get("doc_type"),
        "model_role": state.get("model_role", "primary"),
        "attempt": state.get("parse_attempts", 0) + 1,
        "pages": _page_numbers(state),
    }


def schema_errors(error: ValidationError) -> str:
    """Where and why a reply broke the schema, one line each, for the repair prompt."""
    lines = []
    for e in error.errors(include_url=False, include_input=False):
        where = ".".join(str(part) for part in e["loc"]) or "(the reply)"
        lines.append(f"{where}: {e['msg']}")
    return "\n".join(lines)


@traced("parse", input_of=_parse_input)
def parse(state: GraphState) -> dict:
    doc_type = state["doc_type"]
    model = PARSE_MODELS.get(doc_type)
    if model is None:
        raise ValueError(f"documents of type {doc_type.value} are not parsed")
    name = PARSE_PROMPTS[doc_type]

    step = _step()
    step.set_prompt(name, prompt(name))
    attempts = state.get("parse_attempts", 0) + 1
    try:
        reply = llm.chat(
            [
                {"role": "system", "content": prompt(name)},
                {"role": "user", "content": numbered_document(state["transcriptions"])},
            ],
            max_tokens=PARSE_MAX_TOKENS,
            format=parse_output_schema(doc_type),
            model_role=state.get("model_role", "primary"),
        )
    except llm.OutputCappedError:
        # A cut-off reply is a parse that failed, like one off the schema.
        step.fail(CAPPED)
        return {"parse": None, "parse_error": CAPPED, "parse_attempts": attempts}
    try:
        parsed = model.model_validate_json(reply)
    except ValidationError as e:
        error = schema_errors(e)
        step.fail(error)
        step.set_output({"reply": reply})
        return {"parse": None, "parse_error": error, "parse_attempts": attempts}
    return {"parse": parsed, "parse_error": None, "parse_attempts": attempts}


# -------------------------------------------------------------------
# validate
# -------------------------------------------------------------------
def _validate_input(state: GraphState) -> dict:
    return {
        "doc_type": state.get("doc_type"),
        "parse": state.get("parse"),
        "expected_patient": state.get("expected_patient"),
        "uploaded_by": state.get("uploaded_by"),
        "pages": _page_numbers(state),
    }


def _validate_output(result: dict) -> dict:
    # The pages are in the transcribe steps and the findings in their own
    # column; the record without its pages is what this step adds.
    return {"record": result["record"].model_dump(mode="json", exclude={"pages"})}


@traced("validate", input_of=_validate_input, output_of=_validate_output)
def validate(state: GraphState) -> dict:
    doc_type = state["doc_type"]
    parse_result = state.get("parse")
    if doc_type in PARSE_MODELS and parse_result is None:
        raise ValueError("there is no parse to validate")
    transcriptions = state["transcriptions"]
    record = build_record(
        document_id=state["document_id"],
        doc_type=doc_type,
        pages=transcriptions,
        has_text_layer=any(p.text_layer for p in transcriptions),
        parse=parse_result if doc_type in PARSE_MODELS else None,
        uploaded_by=state.get("uploaded_by"),
    )
    findings = run_validators(
        record, expected_patient=state.get("expected_patient"), today=today()
    )
    _step().add_findings(findings)
    return {"record": record, "findings": findings}


# -------------------------------------------------------------------
# render
# -------------------------------------------------------------------
@traced("render", input_of=lambda s: {"pages": _page_numbers(s)})
def render(state: GraphState) -> dict:
    # Until step 10 renders the record in code: the insight page from the
    # transcription, as the service has made it so far.
    from generate_insights_txt import generate_insights_html

    _step().set_prompt("insight_prompt", prompt("insight_prompt"))
    html = generate_insights_html(
        plain_text(state["transcriptions"]), prompt=prompt("insight_prompt")
    )
    return {"insight_html": html}


# Every node, by the name the graph gives it.
NODES = {
    "open": open_document,
    "transcribe": transcribe,
    "classify": classify,
    "parse": parse,
    "validate": validate,
    "render": render,
}
