"""
Observability without a database: JSONB conversion, prompt hashes, the
node wrapper's guard, model-call accounting, JSON logs and provenance
stamping. The database side is in test_observability_db.py.
"""

import hashlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import llm
from dataset_records import DOC_IDS, key
from observability import (
    JsonFormatter,
    RunError,
    Step,
    configure_logging,
    is_traced,
    prompt_hash,
    stamp,
    traced,
)
from observability import context
from observability.context import ModelCall
from observability.jsonable import jsonable
from record.common import Provenance
from validators import Finding, Severity, Validator

MESSAGES = [{"role": "user", "content": "Transcribe this page."}]


# -------------------------------------------------------------------
# JSONB values
# -------------------------------------------------------------------
class Colour(Enum):
    RED = "red"


@dataclass
class Point:
    x: int
    y: int


def test_jsonable_converts_what_nodes_pass_around():
    finding = Finding(
        validator=Validator.DATES, severity=Severity.FLAG, message="m", lines=[3]
    )
    value = {
        "finding": finding,
        "colour": Colour.RED,
        "when": datetime(2026, 11, 8, 9, 30, tzinfo=timezone.utc),
        "day": date(2026, 11, 8),
        "id": UUID("12345678-1234-5678-1234-567812345678"),
        "path": Path("a") / "b.png",
        "point": Point(1, 2),
        "tags": {"b", "a"},
        "pair": (1, 2.5),
        3: "int key",
    }

    assert jsonable(value) == {
        "finding": finding.model_dump(mode="json"),
        "colour": "red",
        "when": "2026-11-08T09:30:00+00:00",
        "day": "2026-11-08",
        "id": "12345678-1234-5678-1234-567812345678",
        "path": str(Path("a") / "b.png"),
        "point": {"x": 1, "y": 2},
        "tags": ["a", "b"],
        "pair": [1, 2.5],
        "3": "int key",
    }


def test_jsonable_stores_bytes_as_size_and_hash():
    data = b"\x89PNG fake image"
    assert jsonable({"image": data}) == {
        "image": {"$bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    }


def test_jsonable_makes_strings_and_floats_postgres_accepts():
    # jsonb rejects U+0000, and JSON has no NaN or infinity.
    assert jsonable("a\x00b") == "a�b"
    assert jsonable([float("nan"), float("inf"), 1.5]) == ["nan", "inf", 1.5]


def test_jsonable_marks_what_it_cannot_represent_instead_of_failing():
    assert jsonable({"x": object()}) == {"x": {"$unserializable": "object"}}


def test_prompt_hash_is_the_sha256_of_the_text():
    text = "Transcribe the page.\n"
    assert prompt_hash(text) == (
        "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    )
    assert prompt_hash(text) != prompt_hash(text + " ")


# -------------------------------------------------------------------
# The node wrapper
# -------------------------------------------------------------------
def test_a_traced_node_cannot_run_outside_a_run():
    calls = []

    @traced("parse")
    def parse(state):
        calls.append(state)
        return {}

    with pytest.raises(RunError, match="outside a pipeline run"):
        parse({"pages": []})
    assert calls == []


def test_traced_marks_the_node_and_keeps_its_name():
    def classify(state):
        """Pick the document type."""

    wrapped = traced("classify")(classify)

    assert is_traced(wrapped)
    assert not is_traced(classify)
    assert wrapped.__name__ == "classify"
    assert wrapped.__doc__ == "Pick the document type."


# -------------------------------------------------------------------
# Model calls on a step
# -------------------------------------------------------------------
def a_step() -> Step:
    run = SimpleNamespace(id="run-1", trace_id="t" * 32, document_id="doc-1")
    return Step(run, "step-1", 1, "parse", 1, None, None)


def test_a_step_adds_up_tokens_and_keeps_the_model_that_completed():
    step = a_step()
    step.add_model_call(ModelCall("qwen", "primary", 100, 4096, complete=False))
    step.add_model_call(ModelCall("gemma", "fallback", 90, 300, complete=True))

    assert (step.model, step.model_role) == ("gemma", "fallback")
    assert (step.prompt_tokens, step.completion_tokens) == (190, 4396)
    assert step.model_calls == 2


def test_a_step_whose_calls_all_hit_the_cap_names_the_last_model_called():
    step = a_step()
    step.add_model_call(ModelCall("qwen", "primary", None, None, complete=False))
    step.add_model_call(ModelCall("gemma", "fallback", 5, None, complete=False))

    assert (step.model, step.model_role) == ("gemma", "fallback")
    assert (step.prompt_tokens, step.completion_tokens) == (5, None)


def test_a_later_capped_call_does_not_replace_a_completed_one():
    step = a_step()
    step.add_model_call(ModelCall("qwen", "primary", 1, 1, complete=True))
    step.add_model_call(ModelCall("gemma", "fallback", 1, 1, complete=False))

    assert step.model == "qwen"


def test_set_prompt_takes_the_text_or_a_hash():
    step = a_step()
    step.set_prompt("img/extract_page.txt", "Transcribe.")
    assert step.prompt_hash == prompt_hash("Transcribe.")

    step.set_prompt("other", hash="sha256:abc")
    assert (step.prompt_name, step.prompt_hash) == ("other", "sha256:abc")

    with pytest.raises(ValueError):
        step.set_prompt("x")


def test_finding_counts_are_per_severity():
    step = a_step()
    step.add_findings(
        [
            Finding(validator=Validator.DATES, severity=Severity.FLAG, message="a"),
            Finding(
                validator=Validator.SOURCE_TEXT, severity=Severity.ERROR, message="b"
            ),
            {"validator": "coverage", "severity": "info", "message": "c"},
            Finding(validator=Validator.DATES, severity=Severity.FLAG, message="d"),
        ]
    )
    assert step.finding_counts() == {"flag": 2, "error": 1, "info": 1}


class CountingClient:
    """A model client whose last chunk carries Ollama's token counts."""

    def __init__(self, replies):
        self.replies = replies  # model -> list of (text, done_reason) or exception

    def chat(self, *, model, messages, stream, think, options):
        reply = self.replies[model].pop(0)
        if isinstance(reply, BaseException):
            raise reply
        text, done_reason = reply
        return iter(
            [
                SimpleNamespace(
                    message=SimpleNamespace(content=text),
                    done=True,
                    done_reason=done_reason,
                    prompt_eval_count=len(messages) * 10,
                    eval_count=len(text),
                )
            ]
        )


@pytest.fixture
def recording_step():
    step = a_step()
    token = context.current_step.set(step)
    yield step
    context.current_step.reset(token)


def test_llm_reports_the_model_role_and_tokens_to_the_current_step(
    monkeypatch, recording_step
):
    monkeypatch.setattr(
        llm, "_client", CountingClient({llm.PRIMARY_MODEL: [("abc", "stop")]})
    )

    assert llm.chat(MESSAGES, max_tokens=10) == "abc"

    step = recording_step
    assert (step.model, step.model_role) == (llm.PRIMARY_MODEL, "primary")
    assert (step.prompt_tokens, step.completion_tokens) == (10, 3)


def test_llm_reports_a_capped_primary_and_the_fallback_that_answered(
    monkeypatch, recording_step
):
    monkeypatch.setattr(
        llm,
        "_client",
        CountingClient(
            {
                llm.PRIMARY_MODEL: [("cut off", "length")],
                llm.FALLBACK_MODEL: [("whole", "stop")],
            }
        ),
    )

    assert llm.chat(MESSAGES, max_tokens=10) == "whole"

    step = recording_step
    assert (step.model, step.model_role) == (llm.FALLBACK_MODEL, "fallback")
    assert step.model_calls == 2
    assert (step.prompt_tokens, step.completion_tokens) == (20, 12)


def test_llm_reports_nothing_for_a_model_that_raised(monkeypatch, recording_step):
    monkeypatch.setattr(
        llm,
        "_client",
        CountingClient(
            {
                llm.PRIMARY_MODEL: [ConnectionError("refused")],
                llm.FALLBACK_MODEL: [("ok", "stop")],
            }
        ),
    )

    llm.chat(MESSAGES, max_tokens=10)

    assert recording_step.model_calls == 1
    assert recording_step.model_role == "fallback"


def test_llm_works_without_a_step_and_without_token_counts(model_client):
    # The shared fake sends no token counts; nothing is active to report to.
    model_client.replies[llm.PRIMARY_MODEL] = ["fine"]
    assert llm.chat(MESSAGES, max_tokens=10) == "fine"


def test_a_stop_marker_reply_is_reported_without_token_counts(
    model_client, recording_step
):
    model_client.replies[llm.PRIMARY_MODEL] = ["answer END ignored"]

    assert llm.chat(MESSAGES, max_tokens=10, stop_marker="END") == "answer"
    assert recording_step.model == llm.PRIMARY_MODEL
    assert recording_step.prompt_tokens is None


# -------------------------------------------------------------------
# JSON logs
# -------------------------------------------------------------------
def format_record(logger_name="medsum.test", msg="hello", obs=None, exc_info=None):
    record = logging.LogRecord(
        logger_name, logging.INFO, __file__, 1, msg, (), exc_info
    )
    if obs is not None:
        record.obs = obs
    return json.loads(JsonFormatter().format(record))


def test_a_log_line_is_json_with_only_the_allowed_fields():
    line = format_record(
        obs={
            "event": "step_ended",
            "node": "parse",
            "duration_ms": 12,
            "findings": {"error": 1},
            "output": "Haemoglobin 13.2",  # not allowed: dropped
            "input": {"pages": ["..."]},  # not allowed: dropped
            "model": None,  # empty: left out
        }
    )

    assert set(line) == {
        "ts",
        "level",
        "logger",
        "message",
        "event",
        "node",
        "duration_ms",
        "findings",
    }
    assert line["level"] == "INFO"
    assert line["message"] == "hello"
    assert line["ts"].endswith("+00:00")


def test_a_log_line_carries_the_current_run_and_step_ids():
    step = a_step()
    step.parent_id = "step-0"
    token = context.current_step.set(step)
    try:
        line = format_record()
    finally:
        context.current_step.reset(token)

    assert line["run_id"] == "run-1"
    assert line["trace_id"] == "t" * 32
    assert line["document_id"] == "doc-1"
    assert line["step_id"] == line["span_id"] == "step-1"
    assert line["parent_span_id"] == "step-0"
    assert line["node"] == "parse"


def test_an_exception_is_logged_without_its_message():
    try:
        raise ValueError("value 13.2 not on line 4 (Asha Rao)")
    except ValueError:
        import sys

        line = format_record(exc_info=sys.exc_info())

    assert line["exc_type"] == "ValueError"
    assert line["stack"] and "test_observability.py" in line["stack"][-1]
    assert "Asha" not in json.dumps(line)


def test_configure_logging_writes_json_lines_to_the_file(tmp_path, root_logger):
    log_file = tmp_path / "logs" / "medsum.jsonl"

    configure_logging(log_file=str(log_file), console=False)
    logging.getLogger("medsum.test").info("one", extra={"obs": {"event": "x"}})
    logging.getLogger("medsum.test").warning("two")
    for handler in root_logger.handlers:
        handler.flush()

    lines = [json.loads(s) for s in log_file.read_text("utf-8").splitlines()]
    assert [(entry["message"], entry["level"]) for entry in lines] == [
        ("one", "INFO"),
        ("two", "WARNING"),
    ]
    assert lines[0]["event"] == "x"


def test_configure_logging_replaces_its_own_handlers_only(root_logger, tmp_path):
    other = logging.StreamHandler(io.StringIO())
    root_logger.addHandler(other)

    configure_logging(log_file=str(tmp_path / "a.jsonl"))
    configure_logging(log_file=str(tmp_path / "b.jsonl"))

    ours = [h for h in root_logger.handlers if getattr(h, "_medsum_json", False)]
    assert len(ours) == 2  # console and file, once
    assert other in root_logger.handlers
    assert all(isinstance(h.formatter, JsonFormatter) for h in ours)


# -------------------------------------------------------------------
# Provenance stamping
# -------------------------------------------------------------------
def test_stamp_gives_every_item_its_own_copy_of_the_provenance():
    record = key(DOC_IDS[0])
    prov = Provenance(run_id="r", node="parse", step_id="s", model="qwen")

    stamped = stamp(record, prov)

    holders = [record.document, *([record.patient] if record.patient else [])]
    holders += list(record.items())
    assert stamped == len(holders) > 2
    assert all(h.provenance == prov for h in holders)
    assert len({id(h.provenance) for h in holders}) == len(holders)


def test_stamp_keeps_existing_provenance_unless_told_to_overwrite():
    record = key(DOC_IDS[0])
    first = Provenance(run_id="r", node="transcribe", step_id="s1")
    second = Provenance(run_id="r", node="parse", step_id="s2")
    stamp(record.document, first)

    stamp(record, second)
    assert record.document.provenance.node == "transcribe"

    stamp(record, second, overwrite=True)
    assert record.document.provenance.node == "parse"
