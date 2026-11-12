"""
The whole graph (pipeline/graph.py) on dataset documents, with the fake
model client standing in for the models: which path a document takes,
where it ends, what the run and its steps record, and what is saved.
"""

import json
from datetime import date

import pytest

import llm
from dataset.build import DATASET_DIR
from dataset_records import DOCS, expected_patient, key, parse_of
from db_rows import add_account
from observability import is_traced
from pipeline import graph as pipeline_graph
from pipeline import nodes
from pipeline.routing import MAX_PARSE_ATTEMPTS
from pipeline.state import initial_state
from record.common import ItemStatus
from record.document import DocumentRecord

PRIMARY, FALLBACK = llm.PRIMARY_MODEL, llm.FALLBACK_MODEL
TODAY = date(2026, 11, 9)
INSIGHT = "<h2>Summary</h2>"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(nodes, "today", lambda: TODAY)


@pytest.fixture
def document(db):
    """A documents row to run the graph for."""

    def make(doc_id: str) -> dict:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (user_id, original_name, content_type,"
                " storage_path) VALUES (%s, 'a', 'image/png', '/x') RETURNING id",
                (add_account(db),),
            )
            document_id = str(cur.fetchone()[0])
        return initial_state(
            document_id,
            file_path=str(DATASET_DIR / DOCS[doc_id]["file"]),
            uploaded_by="Dr Test",
            expected_patient=expected_patient(doc_id),
        )

    return make


def pages_of(doc_id: str) -> list[str]:
    """Each page's reply from the vision model: the expected transcription."""
    return ["\n".join(p.lines) for p in key(doc_id).pages]


def classified(doc_id: str) -> str:
    return json.dumps({"document_type": DOCS[doc_id]["type"]})


def good_parse(doc_id: str) -> str:
    return parse_of(key(doc_id)).model_dump_json()


def wrong_value(doc_id: str) -> str:
    """A parse whose first result's value is not on its line (validator 1)."""
    reply = json.loads(good_parse(doc_id))
    reply["results"][0]["value_text"] = "99.9"
    return json.dumps(reply)


def run(state):
    return pipeline_graph.run_document(state)


def run_row(db, document_id) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id::text, end_state, error, graph_version FROM pipeline_runs"
            " WHERE document_id = %s",
            (document_id,),
        )
        names = [c.name for c in cur.description]
        return dict(zip(names, cur.fetchone()))


def nodes_run(db, run_id) -> list[str]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT node FROM pipeline_steps WHERE run_id = %s ORDER BY seq", (run_id,)
        )
        return [r[0] for r in cur.fetchall()]


def saved(db, document_id) -> DocumentRecord | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT record FROM document_records WHERE document_id = %s", (document_id,)
        )
        row = cur.fetchone()
    return DocumentRecord.model_validate(row[0]) if row else None


# -------------------------------------------------------------------
# The graph
# -------------------------------------------------------------------
def test_every_node_of_the_compiled_graph_is_traced():
    compiled = pipeline_graph.build()
    names = set(compiled.get_graph().nodes) - {"__start__", "__end__"}
    assert names == set(nodes.NODES)
    assert all(is_traced(nodes.NODES[n]) for n in names)


# -------------------------------------------------------------------
# Paths through it
# -------------------------------------------------------------------
def test_a_clean_prescription_is_verified(db, document, model_client):
    state = document("d02")
    model_client.replies[PRIMARY] = [
        *pages_of("d02"), classified("d02"), good_parse("d02"), INSIGHT,
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    row = run_row(db, state["document_id"])
    assert (row["end_state"], row["graph_version"]) == (
        "verified", pipeline_graph.GRAPH_VERSION
    )  # fmt: skip
    assert nodes_run(db, row["id"]) == [
        "open", "transcribe", "classify", "parse", "validate", "render", "finish",
    ]  # fmt: skip
    record = saved(db, state["document_id"])
    assert record == final["record"]
    assert all(i.provenance.status is ItemStatus.VALIDATED for i in record.items())
    assert INSIGHT in final["insight_html"]
    # An image (no text layer) and lines no item uses: shown, never routed.
    assert {h.signal for h in final["signals"]} == {"LC-I1", "LC-I2"}


def test_every_page_of_a_lab_report_is_transcribed(db, document, model_client):
    state = document("d01")
    model_client.replies[PRIMARY] = [
        *pages_of("d01"), classified("d01"), good_parse("d01"), INSIGHT,
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    assert len(final["record"].pages) == DOCS["d01"]["pages"] == 3
    steps = nodes_run(db, run_row(db, state["document_id"])["id"])
    assert steps.count("transcribe") == 3


def test_an_error_is_repaired(db, document, model_client):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"), good_parse("d09"),
        INSIGHT,
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "verified"
    assert final["repairs"] == 1
    assert nodes_run(db, run_row(db, state["document_id"])["id"])[3:7] == [
        "parse", "validate", "repair", "validate"
    ]  # fmt: skip
    first = final["record"].observations[0]
    assert first.provenance.node == "repair"
    assert "OP-2" in [h.signal for h in final["signals"]]


def test_the_fallback_takes_over_when_the_primary_s_tries_are_used(
    db, document, model_client
):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"),
        *[wrong_value("d09")] * MAX_PARSE_ATTEMPTS, INSIGHT,
    ]  # fmt: skip
    model_client.replies[FALLBACK] = [good_parse("d09")]

    final = run(state)

    assert final["end_state"] == "verified"
    steps = nodes_run(db, run_row(db, state["document_id"])["id"])
    assert steps.count("repair") == MAX_PARSE_ATTEMPTS - 1
    assert steps[-5:] == ["fallback", "parse", "validate", "render", "finish"]
    assert final["record"].document.provenance.model == FALLBACK
    assert "OP-1" in [h.signal for h in final["signals"]]  # shown, not routed


def test_an_error_left_by_both_models_goes_to_review(db, document, model_client):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"),
        *[wrong_value("d09")] * MAX_PARSE_ATTEMPTS, INSIGHT,
    ]  # fmt: skip
    model_client.replies[FALLBACK] = [wrong_value("d09")] * MAX_PARSE_ATTEMPTS

    final = run(state)

    assert final["end_state"] == "needs_review"
    assert run_row(db, state["document_id"])["end_state"] == "needs_review"
    first = saved(db, state["document_id"]).observations[0]
    assert first.provenance.status is ItemStatus.LOW_CONFIDENCE
    assert first.provenance.low_confidence_reasons[0].startswith("LC-04")


def test_replies_that_never_fit_the_schema_leave_the_pages_for_review(
    db, document, model_client
):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), *['{"patient":'] * MAX_PARSE_ATTEMPTS,
        INSIGHT,
    ]  # fmt: skip
    model_client.replies[FALLBACK] = ['{"patient":'] * MAX_PARSE_ATTEMPTS

    final = run(state)

    assert final["end_state"] == "needs_review"
    record = saved(db, state["document_id"])
    assert record.patient is None and record.pages == final["transcriptions"]
    assert record.document.provenance.node == "finish"
    assert record.document.provenance.low_confidence_reasons[0].startswith("LC-08")


def test_a_later_failed_reply_keeps_the_last_record_validated(
    db, document, model_client
):
    state = document("d09")
    model_client.replies[PRIMARY] = [
        *pages_of("d09"), classified("d09"), wrong_value("d09"),
        *['{"patient":'] * (MAX_PARSE_ATTEMPTS - 1), INSIGHT,
    ]  # fmt: skip
    model_client.replies[FALLBACK] = ['{"patient":'] * MAX_PARSE_ATTEMPTS

    final = run(state)

    assert final["end_state"] == "needs_review"
    record = saved(db, state["document_id"])
    assert record.observations[0].printed.value_text == "99.9"
    assert record.observations[0].provenance.node == "parse"


def test_a_document_of_type_other_goes_to_review_unparsed(db, document, model_client):
    state = document("d17")
    model_client.replies[PRIMARY] = [
        *pages_of("d17"), '{"document_type": "other"}', INSIGHT,
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "needs_review"
    steps = nodes_run(db, run_row(db, state["document_id"])["id"])
    assert "parse" not in steps and steps[-3:] == ["validate", "render", "finish"]
    assert "LC-14" in [h.signal for h in final["signals"]]


def test_a_document_for_another_patient_goes_to_review(db, document, model_client):
    state = document("d16")
    model_client.replies[PRIMARY] = [
        *pages_of("d16"), classified("d16"), good_parse("d16"), INSIGHT,
    ]  # fmt: skip

    final = run(state)

    assert final["end_state"] == "needs_review"
    assert "LC-13" in [h.signal for h in final["signals"]]
    assert "repair" not in nodes_run(db, run_row(db, state["document_id"])["id"])


# -------------------------------------------------------------------
# Failures
# -------------------------------------------------------------------
def test_no_model_answering_fails_the_run_with_the_error(db, document, model_client):
    state = document("d02")
    model_client.replies[PRIMARY] = [ConnectionError("down")]
    model_client.replies[FALLBACK] = [ConnectionError("down")]

    with pytest.raises(llm.ModelOutputError):
        run(state)

    row = run_row(db, state["document_id"])
    assert row["end_state"] == "failed" and "ModelOutputError" in row["error"]
    assert saved(db, state["document_id"]) is None


def test_an_unreadable_file_fails_the_run(db, document, model_client, tmp_path):
    state = document("d02")
    state["file_path"] = str(tmp_path / "missing.png")

    with pytest.raises(Exception):
        run(state)

    assert run_row(db, state["document_id"])["end_state"] == "failed"
    assert model_client.calls == []
