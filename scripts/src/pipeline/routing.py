"""
Where the graph goes after each node: small pure functions of the state,
each returning the next node's name.

    open -> transcribe (one page per visit, until every page is done)
         -> classify -> parse          (lab report, prescription)
                     -> validate       (other: a record of its pages only)
    parse / repair -> validate         (the reply fit the schema)
                   -> retry            (it didn't, or was cut off)
    validate -> retry                  (error findings: the parse may be wrong)
             -> render                 (no errors, or nothing left to try)
    render -> finish -> end

retry: a repair while the model in use has attempts left (MAX_PARSE_ATTEMPTS:
the first parse and two repairs, decision 19); then the fallback model, once,
with the same budget; then give up. Giving up keeps the last record that was
validated; if there is none, validate builds a record of the pages alone,
and the document goes to review (signal LC-08).

Only error findings (validators 1, 5 and 6) send a parse back: a flag is
about the document itself, which a re-parse can't change, so it goes to a
clinician at once (section 8.8).
"""

from pipeline.state import GraphState
from record.printed import PARSE_MODELS
from validators import Severity

# The first parse plus up to two repairs, per model (decision 19).
MAX_PARSE_ATTEMPTS = 3


def after_transcribe(state: GraphState) -> str:
    done = {p.page for p in state.get("transcriptions", [])}
    pending = [p for p in state.get("pages", []) if p.page not in done]
    return "transcribe" if pending else "classify"


def after_classify(state: GraphState) -> str:
    return "parse" if state["doc_type"] in PARSE_MODELS else "validate"


def after_parse(state: GraphState) -> str:
    """After parse or repair."""
    if state.get("parse") is not None:
        return "validate"
    return retry(state)


def after_validate(state: GraphState) -> str:
    if state.get("record_parsed") and has_errors(state):
        return retry(state)
    return "render"


def has_errors(state: GraphState) -> bool:
    return any(f.severity is Severity.ERROR for f in state.get("findings", []))


def retry(state: GraphState) -> str:
    """The next try at a parse, or where to go when there is none left."""
    if state.get("parse_attempts", 0) < MAX_PARSE_ATTEMPTS:
        return "repair"
    if state.get("model_role", "primary") == "primary":
        return "fallback"
    return "render" if state.get("record_parsed") else "validate"
