"""
The graph's state: its starting values, the page-merging reducer, the
types the checkpointer may restore, and that every dataset record and its
findings come back from a checkpoint exactly as they went in.
"""

import importlib
import logging
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError

from dataset_records import DOC_IDS, PARSED_IDS, expected_patient, key, parse_of
from pipeline import checkpoints
from pipeline.state import (
    GraphState,
    PageImage,
    checkpoint_types,
    initial_state,
    merge_pages,
)
from record.document import PageTranscription
from record.printed import DocumentType
from validators import ExpectedPatient, validate

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def page(n: int, *lines: str) -> PageTranscription:
    return PageTranscription(page=n, lines=list(lines) or [f"page {n}"])


# -------------------------------------------------------------------
# Starting state
# -------------------------------------------------------------------
def test_a_run_starts_with_empty_results_on_the_primary_model():
    patient = ExpectedPatient(name="Dr Test")

    state = initial_state(
        "doc-1", file_path="/up/a.pdf", uploaded_by="Dr Test", expected_patient=patient
    )

    assert state == {
        "document_id": "doc-1",
        "file_path": "/up/a.pdf",
        "uploaded_by": "Dr Test",
        "expected_patient": patient,
        "transcriptions": [],
        "findings": [],
        "part_results": [],
        "parse_attempts": 0,
        "model_role": "primary",
    }


def test_the_document_id_is_kept_as_text():
    import uuid

    doc = uuid.uuid4()
    assert initial_state(doc)["document_id"] == str(doc)


def test_a_page_is_kept_without_its_image():
    image = PageImage(
        page=1, sha256="ab" * 32, size_bytes=1234, width=1654, height=2339
    )
    assert image.size_bytes == 1234

    with pytest.raises(ValidationError):
        PageImage(page=1, sha256="x", size_bytes=1, width=1, height=1, png=b"...")
    with pytest.raises(ValidationError):
        PageImage(page=0, sha256="x", size_bytes=1, width=1, height=1)


# -------------------------------------------------------------------
# Merging transcriptions
# -------------------------------------------------------------------
def test_new_pages_are_added_in_page_order():
    merged = merge_pages([page(2)], [page(3), page(1)])
    assert [p.page for p in merged] == [1, 2, 3]


def test_a_page_transcribed_again_replaces_the_old_one():
    merged = merge_pages([page(1, "old"), page(2, "keep")], [page(1, "new")])
    assert [(p.page, p.lines) for p in merged] == [(1, ["new"]), (2, ["keep"])]


@pytest.mark.parametrize(
    "current, update, expected",
    [(None, None, []), (None, [page(1)], [1]), ([page(1)], None, [1]), ([], [], [])],
)
def test_merging_handles_missing_sides(current, update, expected):
    assert [p.page for p in merge_pages(current, update)] == expected


def test_a_graph_merges_pages_returned_one_at_a_time():
    # One page per node call, as a transcribe loop or a repair would:
    # page 2, then page 1, then page 2 again.
    queue = [(2, "first reading"), (1, "page one"), (2, "second reading")]

    def transcribe(state):
        n, text = queue.pop(0)
        return {"transcriptions": [page(n, text)]}

    builder = StateGraph(GraphState)
    builder.add_node("transcribe", transcribe)
    builder.add_edge(START, "transcribe")
    builder.add_conditional_edges(
        "transcribe", lambda s: "transcribe" if queue else END
    )

    result = builder.compile().invoke(initial_state("doc-1"))

    assert [(p.page, p.lines) for p in result["transcriptions"]] == [
        (1, ["page one"]),
        (2, ["second reading"]),
    ]


# -------------------------------------------------------------------
# Checkpoint types
# -------------------------------------------------------------------
def test_the_allowlist_covers_nested_models_enums_and_dataclasses():
    types = set(checkpoint_types())

    assert {
        ("record.document", "DocumentRecord"),
        ("record.document", "PageTranscription"),
        ("record.printed", "LabReportParse"),
        ("record.printed", "PrescriptionParse"),
        ("validators.findings", "Finding"),
        ("pipeline.state", "PageImage"),
        # nested in the ones above
        ("record.common", "Citation"),
        ("record.common", "Provenance"),
        ("record.document", "DateSource"),
        ("record.printed", "DocumentType"),
        ("validators.findings", "Severity"),
        # a dataclass, and the date inside it is a builtin type
        ("validators.patient", "ExpectedPatient"),
    } <= types


def test_every_allowed_type_exists():
    for module, name in checkpoint_types():
        assert hasattr(importlib.import_module(module), name), (module, name)


def full_state(doc_id: str) -> dict:
    record = key(doc_id)
    return {
        **initial_state(doc_id, expected_patient=expected_patient(doc_id)),
        "doc_type": record.document.type,
        "pages_in_file": len(record.pages),
        "pages": [
            PageImage(page=p.page, sha256="0" * 64, size_bytes=1, width=1, height=1)
            for p in record.pages
        ],
        "transcriptions": record.pages,
        "parse": parse_of(record),
        "parse_error": None,
        "parse_attempts": 1,
        "record": record,
        "findings": validate(
            record, expected_patient=expected_patient(doc_id), today=date(2026, 11, 9)
        ),
        "insight_html": "<html></html>",
        "end_state": "needs_review",
        "error": None,
    }


@pytest.fixture
def serde_warnings(caplog):
    caplog.set_level(logging.WARNING, logger="langgraph")
    yield caplog
    assert [
        r.getMessage() for r in caplog.records if r.name.startswith("langgraph")
    ] == []


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_a_dataset_state_round_trips_through_the_serializer(doc_id, serde_warnings):
    state = full_state(doc_id)
    serde = checkpoints.serializer()

    back = serde.loads_typed(serde.dumps_typed(state))

    assert back == state
    assert type(back["record"]) is type(state["record"])
    assert type(back["doc_type"]) is DocumentType
    if doc_id in PARSED_IDS:
        assert type(back["parse"]) is type(state["parse"])
    assert all(type(f) is type(g) for f, g in zip(back["findings"], state["findings"]))
    assert isinstance(back["expected_patient"], ExpectedPatient)


class NotInTheState(BaseModel):
    value: str


def test_a_type_outside_the_state_is_not_restored(caplog):
    serde = checkpoints.serializer()
    caplog.set_level(logging.WARNING, logger="langgraph")

    back = serde.loads_typed(serde.dumps_typed({"x": NotInTheState(value="v")}))

    assert not isinstance(back["x"], NotInTheState)
    assert any("Blocked" in r.getMessage() for r in caplog.records)


def test_a_dataset_state_comes_back_from_a_postgres_checkpoint(
    db, database_url, serde_warnings
):
    doc_id = PARSED_IDS[0]
    state = full_state(doc_id)

    builder = StateGraph(GraphState)
    builder.add_node(
        "load", lambda s: {k: v for k, v in state.items() if k != "document_id"}
    )
    builder.add_edge(START, "load")
    builder.add_edge("load", END)
    config = {"configurable": {"thread_id": "state-test"}}

    with checkpoints.open_checkpointer(database_url) as saver:
        builder.compile(checkpointer=saver).invoke(initial_state(doc_id), config)
    with checkpoints.open_checkpointer(database_url) as saver:
        restored = builder.compile(checkpointer=saver).get_state(config).values

    assert restored == state


# -------------------------------------------------------------------
# Local only
# -------------------------------------------------------------------
def test_importing_the_pipeline_switches_langsmith_tracing_off():
    env = {**os.environ, "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true",
           "LANGSMITH_API_KEY": "not-a-key"}  # fmt: skip
    code = (
        "import langsmith.utils as u, pipeline;"
        "from langchain_core.tracers.context import _tracing_v2_is_enabled as v2;"
        "print(u.tracing_is_enabled(), v2())"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=SRC_DIR, env=env,
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert out.stdout.split() == ["False", "False"]
