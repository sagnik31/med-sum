"""
A document processed again reuses its earlier run's transcription of a
page when the page image, the model and the prompt are the same: no
model call, and the step says which step it took the lines from.
"""

import psycopg
import pytest

import llm
from db_rows import add_account
from observability import start_run
from pipeline import nodes, store
from pipeline import pages as page_files
from pipeline.state import initial_state
from test_pipeline_nodes import file_of


def new_document(db, email: str = "clinician@hospital.test") -> str:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, 'a.pdf', 'application/pdf', '/x') RETURNING id",
            (add_account(db, email),),
        )
        return str(cur.fetchone()[0])


def opened(document_id: str, doc: str = "d09") -> dict:
    path = file_of(doc)
    n = page_files.page_count(path)
    state = initial_state(document_id, file_path=str(path))
    state["pages_in_file"] = n
    state["pages"] = [
        page_files.describe(i, page_files.page_png(path, i)) for i in range(1, n + 1)
    ]
    return state


def transcribe_in_a_run(document_id: str, doc: str = "d09"):
    with start_run(document_id, "test-reuse") as run:
        out = nodes.transcribe(opened(document_id, doc))
        run.finish("verified")
    return run, out["transcriptions"][0]


def steps(db, run) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, status, model, model_role, prompt_tokens, output"
            " FROM pipeline_steps WHERE run_id = %s ORDER BY seq",
            (run.id,),
        )
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


@pytest.fixture
def first(db, model_client):
    """A document whose page 1 was transcribed once, by the primary model."""
    doc = new_document(db)
    model_client.replies[llm.PRIMARY_MODEL] = ["# Sunrise\n\nName: A"]
    run, page = transcribe_in_a_run(doc)
    assert len(model_client.calls) == 1
    return doc, run, page


def test_the_second_run_uses_the_first_runs_lines_without_a_model_call(
    db, model_client, first
):
    doc, run1, page1 = first

    run2, page2 = transcribe_in_a_run(doc)

    assert len(model_client.calls) == 1  # still only the first run's call
    assert page2.lines == page1.lines == ["# Sunrise", "", "Name: A"]
    assert page2.text_layer == page_files.text_layer(file_of("d09"), 1)
    [earlier] = steps(db, run1)
    [row] = steps(db, run2)
    assert row["status"] == "ok"
    assert (row["model"], row["model_role"]) == (llm.PRIMARY_MODEL, "primary")
    assert row["prompt_tokens"] is None
    assert row["output"]["reused_from_step"] == str(earlier["id"])
    assert row["output"]["transcriptions"][0]["lines"] == page1.lines


def test_the_step_log_names_the_step_it_reused(db, model_client, first, json_log):
    doc, run1, _ = first

    transcribe_in_a_run(doc)

    [earlier] = steps(db, run1)
    assert f'"reused_from_step": "{earlier["id"]}"' in json_log.getvalue()


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE pipeline_steps SET prompt_hash = 'another prompt'",
        "UPDATE pipeline_steps SET model = 'another-model'",
        f"UPDATE pipeline_steps SET model = '{llm.FALLBACK_MODEL}',"
        " model_role = 'fallback'",
        "UPDATE pipeline_steps SET input = jsonb_set(input, '{image,sha256}',"
        " '\"0000\"')",
        "UPDATE pipeline_steps SET status = 'failed'",
    ],
    ids=["other prompt", "other model", "fallback model", "other image", "failed"],
)
def test_a_page_is_read_again_unless_everything_matches(
    db, model_client, first, change
):
    doc, _, _ = first
    with db.cursor() as cur:
        cur.execute(change)
    model_client.replies[llm.PRIMARY_MODEL] = ["read again"]

    _, page = transcribe_in_a_run(doc)

    assert len(model_client.calls) == 2
    assert page.lines == ["read again"]


def test_another_documents_reading_of_the_same_page_is_not_used(
    db, model_client, first
):
    other = new_document(db, "other@hospital.test")
    model_client.replies[llm.PRIMARY_MODEL] = ["read again"]

    _, page = transcribe_in_a_run(other)

    assert len(model_client.calls) == 2
    assert page.lines == ["read again"]


def test_a_capped_page_is_read_again(db, model_client):
    doc = new_document(db)
    # Capped on both models, then capped again in its top half.
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off", "length")] * 2
    model_client.replies[llm.FALLBACK_MODEL] = [("cut off", "length")] * 2
    _, page = transcribe_in_a_run(doc)
    assert page.lines == []
    model_client.replies[llm.PRIMARY_MODEL] = ["whole page"]

    _, page = transcribe_in_a_run(doc)

    assert page.lines == ["whole page"]


def test_a_failed_lookup_reads_the_page_again(db, model_client, first, monkeypatch):
    doc, _, _ = first

    def broken(*a, **k):
        raise psycopg.OperationalError("database gone")

    monkeypatch.setattr(store, "earlier_transcription", broken)
    model_client.replies[llm.PRIMARY_MODEL] = ["read again"]

    _, page = transcribe_in_a_run(doc)

    assert page.lines == ["read again"]


def test_a_stored_output_of_the_wrong_shape_is_not_used(db, model_client, first):
    doc, _, _ = first
    with db.cursor() as cur:
        cur.execute("""UPDATE pipeline_steps SET output = '{"transcriptions": []}'""")
    model_client.replies[llm.PRIMARY_MODEL] = ["read again"]

    _, page = transcribe_in_a_run(doc)

    assert page.lines == ["read again"]
