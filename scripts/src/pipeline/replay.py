"""
Run one stored model step again and show how its reply differs:

    python -m pipeline.replay <step_id>

The step's model call is rebuilt from what the run stored: the prompt text
by the step's prompt hash (the prompts table), the step's input, and the
run's transcriptions (its transcribe steps' outputs) or, for a transcribe
step, the page rendered again from the document's file, checked against
the image hash the step stored. The call goes to the models configured now
(the stored model and the one that answered are both shown), and nothing
is written to the database.

Transcribe, classify and parse steps can be replayed. A repair isn't: its
conversation includes the problems the validators found, which no step
stores as text. A transcribe step that read its page again in halves is
replayed as the single whole-page call.

It prints document content to the terminal, as a developer's tool; none
of it is logged. See docs/adr/0006-durable-traceable-runs.md.
"""

import argparse
import difflib
import json
import sys
from dataclasses import dataclass, field

import psycopg

import llm
from observability import context
from observability.tracing import prompt_hash
from pipeline import pages as page_files
from pipeline import parts
from pipeline.nodes import (
    CAPPED,
    CLASSIFY_CHARS,
    CLASSIFY_MAX_TOKENS,
    PARSE_MAX_TOKENS,
    lines_of,
    plain_text,
)
from pipeline.prompts import (
    END_MARKER,
    FILES,
    MAX_TOKENS_PER_PAGE,
    prompt,
    transcription_messages,
)
from pipeline.state import PageTranscription
from pipeline.store import connect as default_connect
from record.printed import ClassifierOutput, DocumentType
from record.schemas import classifier_schema, parse_output_schema

REPLAYABLE = ("transcribe", "classify", "parse")
CAPPED_TEXT = f"[{CAPPED}]"


class ReplayError(RuntimeError):
    """The step can't be replayed; the message says why."""


@dataclass
class Replay:
    step_id: str
    node: str
    page: int | None
    stored_model: str | None
    prompt_name: str
    prompt_current: bool  # the stored prompt is the prompt file's text today
    stored: str
    replayed: str
    models: list[str] = field(default_factory=list)  # that answered the replay

    @property
    def same(self) -> bool:
        return self.stored == self.replayed

    def diff(self) -> list[str]:
        return list(
            difflib.unified_diff(
                self.stored.splitlines(),
                self.replayed.splitlines(),
                "stored",
                "replayed",
                lineterm="",
            )
        )


class _Calls:
    """Stands in for a step, to learn which model answered the replay."""

    def __init__(self):
        self.models: list[str] = []

    def add_model_call(self, call) -> None:
        if call.status != "failed" and call.model not in self.models:
            self.models.append(call.model)


_STEP = """
SELECT s.node, s.page, s.status, s.model, s.prompt_name, s.prompt_hash,
       s.input, s.output, s.error, s.run_id::text, d.storage_path
FROM pipeline_steps s
JOIN pipeline_runs r ON r.id = s.run_id
JOIN documents d ON d.id = r.document_id
WHERE s.id = %s
"""

_TRANSCRIPTIONS = """
SELECT DISTINCT ON (page) page, output
FROM pipeline_steps
WHERE run_id = %s AND node = 'transcribe' AND output ? 'transcriptions'
ORDER BY page, seq DESC
"""


def _step(cur, step_id: str) -> dict:
    try:
        cur.execute(_STEP, (step_id,))
    except psycopg.errors.InvalidTextRepresentation:
        raise ReplayError(f"{step_id!r} is not a step id") from None
    row = cur.fetchone()
    if row is None:
        raise ReplayError(f"no pipeline step {step_id}")
    names = [c.name for c in cur.description]
    return dict(zip(names, row))


def _prompt_text(cur, step: dict) -> str:
    if step["prompt_hash"] is None:
        raise ReplayError(f"the {step['node']} step used no prompt")
    cur.execute("SELECT text FROM prompts WHERE hash = %s", (step["prompt_hash"],))
    row = cur.fetchone()
    if row is None:
        raise ReplayError(
            "the step's prompt text was not stored (it ran before prompts were)"
        )
    return row[0]


def _transcriptions(cur, run_id: str, wanted: list[int]) -> list[PageTranscription]:
    """The run's transcription of each page in `wanted`, in page order."""
    cur.execute(_TRANSCRIPTIONS, (run_id,))
    found = {
        page: PageTranscription.model_validate(output["transcriptions"][0])
        for page, output in cur.fetchall()
    }
    missing = [n for n in wanted if n not in found]
    if missing:
        raise ReplayError(f"the run stored no transcription of pages {missing}")
    return [found[n] for n in wanted]


def _current(name: str, stored_hash: str) -> bool:
    base = name.split("+")[0]
    return base in FILES and prompt_hash(prompt(base)) == stored_hash


def _json_text(reply: str | None) -> str:
    """A JSON reply laid out one key per line, so a diff shows what changed."""
    if reply is None:
        return CAPPED_TEXT
    try:
        return json.dumps(json.loads(reply), indent=2, ensure_ascii=False)
    except ValueError:
        return reply


# ------------------------------------------------------------------
# One rebuild per node: (stored text, a function making the call)
# ------------------------------------------------------------------
def _transcribe(cur, step: dict, text: str):
    n = step["page"]
    image = page_files.page_png(step["storage_path"], n)
    stored_image = (step["input"] or {}).get("image") or {}
    if page_files.describe(n, image).sha256 != stored_image.get("sha256"):
        raise ReplayError(f"page {n} of the file has changed since the step ran")
    lines = step["output"]["transcriptions"][0]["lines"]
    stored = CAPPED_TEXT if not lines and step["error"] else "\n".join(lines)

    def call() -> str:
        try:
            reply = llm.chat(
                transcription_messages(image, text),
                max_tokens=MAX_TOKENS_PER_PAGE,
                stop_marker=END_MARKER,
            )
        except llm.OutputCappedError:
            return CAPPED_TEXT
        return "\n".join(lines_of(reply))

    return stored, call


def _classify(cur, step: dict, text: str):
    pages = _transcriptions(cur, step["run_id"], step["input"]["pages"])
    document = plain_text(pages)[:CLASSIFY_CHARS]
    output = step["output"] or {}
    stored = str(output.get("model_label") or output.get("model_error"))

    def call() -> str:
        try:
            reply = llm.chat(
                [
                    {"role": "system", "content": text},
                    {"role": "user", "content": document},
                ],
                max_tokens=CLASSIFY_MAX_TOKENS,
                format=classifier_schema(),
            )
            return ClassifierOutput.model_validate_json(reply).document_type
        except (ValueError, llm.ModelOutputError) as e:
            return type(e).__name__

    return stored, call


def _parse(cur, step: dict, text: str):
    given = step["input"]
    part = parts.ParsePart.model_validate(given["part"])
    pages = _transcriptions(cur, step["run_id"], given["pages"])
    doc_type = DocumentType(given["doc_type"])
    messages = [
        {"role": "system", "content": text},
        {"role": "user", "content": parts.part_text(pages, part)},
    ]
    results = (step["output"] or {}).get("part_results") or [{}]
    stored = _json_text(results[0].get("reply"))

    def call() -> str:
        try:
            reply = llm.chat(
                messages,
                max_tokens=PARSE_MAX_TOKENS,
                format=parse_output_schema(doc_type),
                model_role=given.get("model_role", "primary"),
            )
        except llm.OutputCappedError:
            return CAPPED_TEXT
        return _json_text(reply)

    return stored, call


_REBUILD = {"transcribe": _transcribe, "classify": _classify, "parse": _parse}


def replay(step_id: str, *, connect=default_connect) -> Replay:
    """Run a stored step's model call again; see the module's docstring."""
    with connect() as conn, conn.cursor() as cur:
        step = _step(cur, step_id)
        if step["node"] not in REPLAYABLE:
            raise ReplayError(
                f"a {step['node']} step can't be replayed; only"
                f" {', '.join(REPLAYABLE)} steps can"
            )
        if step["status"] == "running":
            raise ReplayError("the step is still running")
        if step["output"] is None:
            raise ReplayError("the step stored no output to compare with")
        text = _prompt_text(cur, step)
        stored, call = _REBUILD[step["node"]](cur, step, text)
    calls = _Calls()
    token = context.current_step.set(calls)
    try:
        replayed = call()
    finally:
        context.current_step.reset(token)
    return Replay(
        step_id=step_id,
        node=step["node"],
        page=step["page"],
        stored_model=step["model"],
        prompt_name=step["prompt_name"],
        prompt_current=_current(step["prompt_name"], step["prompt_hash"]),
        stored=stored,
        replayed=replayed,
        models=calls.models,
    )


def report(r: Replay) -> str:
    """What the command prints: the step, its models, and the diff."""
    where = f" page {r.page}" if r.page is not None else ""
    lines = [
        f"step {r.step_id}: {r.node}{where}",
        f"prompt {r.prompt_name}"
        + ("" if r.prompt_current else " (changed in the prompt files since)"),
        f"stored model {r.stored_model}; replayed on {', '.join(r.models) or 'none'}",
    ]
    if r.same:
        lines.append("the reply is the same")
    else:
        lines.extend(r.diff())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.replay", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("step_id", help="a pipeline_steps id")
    args = parser.parse_args(argv)
    try:
        r = replay(args.step_id)
    except ReplayError as e:
        print(f"can't replay: {e}", file=sys.stderr)
        return 2
    print(report(r))
    return 0 if r.same else 1


if __name__ == "__main__":
    sys.exit(main())
