"""
Run provenance and replay: what a run records about the code and database
it ran on, the prompts table, and python -m pipeline.replay on the steps of
a graph run made with the fake model client.
"""

import json
import re
import uuid

import pytest

import llm
from dataset.build import DATASET_DIR
from dataset_records import DOCS, expected_patient, key, parse_of
from db_rows import add_account
from observability import prompt_hash, start_run, traced, versions
from pipeline import graph as pipeline_graph
from pipeline import replay as replay_mod
from pipeline.prompts import prompt
from pipeline.replay import CAPPED_TEXT, ReplayError, replay
from pipeline.state import initial_state

PRIMARY = llm.PRIMARY_MODEL
DOC = "d02"  # a one-page prescription: transcribe, classify, one parse


@pytest.fixture
def document(db):
    """A documents row whose file is a dataset document, and its state."""

    def make(doc_id: str, *, stored_file: str | None = None) -> dict:
        path = str(DATASET_DIR / DOCS[doc_id]["file"])
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (user_id, original_name, content_type,"
                " storage_path) VALUES (%s, 'a', 'application/pdf', %s)"
                " RETURNING id",
                (
                    add_account(db, f"{uuid.uuid4().hex}@hospital.test"),
                    stored_file or path,
                ),
            )
            document_id = str(cur.fetchone()[0])
        return initial_state(
            document_id,
            file_path=path,
            uploaded_by="Dr Test",
            expected_patient=expected_patient(doc_id),
        )

    return make


def page_text(doc_id: str) -> str:
    return "\n".join(key(doc_id).pages[0].lines)


def classified(doc_id: str) -> str:
    return json.dumps({"document_type": DOCS[doc_id]["type"]})


def good_parse(doc_id: str) -> str:
    return parse_of(key(doc_id)).model_dump_json()


@pytest.fixture
def ran(db, document, model_client):
    """A verified run of DOC; returns {node: step id}."""
    state = document(DOC)
    model_client.replies[PRIMARY] = [page_text(DOC), classified(DOC), good_parse(DOC)]
    assert pipeline_graph.run_document(state)["end_state"] == "verified"
    with db.cursor() as cur:
        cur.execute(
            "SELECT s.node, s.id::text FROM pipeline_steps s"
            " JOIN pipeline_runs r ON r.id = s.run_id WHERE r.document_id = %s",
            (state["document_id"],),
        )
        return dict(cur.fetchall())


def one(db, sql, *params):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# -------------------------------------------------------------------
# What a run records
# -------------------------------------------------------------------
def test_a_run_records_its_commit_output_schemas_and_database_structure(db, ran):
    commit, dirty, schema, db_schema = one(
        db,
        "SELECT code_version, code_dirty, schema_hash, db_schema_hash"
        " FROM pipeline_runs",
    )
    found = versions.code_version()
    assert (commit, dirty) == (found["commit"], found["dirty"])
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert schema == versions.output_schema_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", schema)
    with db.cursor() as cur:
        assert db_schema == versions.db_schema_hash(cur)


def test_the_output_schema_hash_follows_what_the_models_are_sent(monkeypatch):
    from record import schemas

    before = versions.output_schema_hash()
    versions.output_schema_hash.cache_clear()
    real = schemas.classifier_schema
    monkeypatch.setattr(schemas, "classifier_schema", lambda: {**real(), "x": 1})
    try:
        assert versions.output_schema_hash() != before
    finally:
        versions.output_schema_hash.cache_clear()


def test_a_given_code_version_has_no_dirty_flag(db, document, monkeypatch):
    monkeypatch.setenv(versions.CODE_VERSION_ENV, "v1.2.3")
    state = document(DOC)
    with start_run(state["document_id"], "test-1") as run:
        run.finish("failed")
    assert one(db, "SELECT code_version, code_dirty FROM pipeline_runs") == (
        "v1.2.3", None,
    )  # fmt: skip


def test_without_git_the_code_version_is_unknown(tmp_path):
    assert versions.code_version(tmp_path) is None


def test_the_schema_hash_follows_the_tables_and_columns(db):
    with db.cursor() as cur:
        before = versions.db_schema_hash(cur)
        assert versions.db_schema_hash(cur) == before
        cur.execute("CREATE TABLE public.provenance_probe (x integer)")
        try:
            with_table = versions.db_schema_hash(cur)
            cur.execute("ALTER TABLE public.provenance_probe ADD COLUMN y text")
            with_column = versions.db_schema_hash(cur)
        finally:
            cur.execute("DROP TABLE public.provenance_probe")
        assert len({before, with_table, with_column}) == 3
        assert versions.db_schema_hash(cur) == before


def test_every_prompt_a_run_used_is_stored_once_by_its_hash(
    db, ran, document, model_client
):
    rows = dict(
        (name, (h, text))
        for h, name, text in _all(db, "SELECT hash, name, text FROM prompts")
    )
    parse_prompt = "parse_prescription"
    assert set(rows) == {"extract_page", "classify_document", parse_prompt}
    for name, (h, text) in rows.items():
        assert text == prompt(name)
        assert h == prompt_hash(text)
    step_hashes = {h for (h,) in _all(db, "SELECT DISTINCT prompt_hash FROM"
                                          " pipeline_steps WHERE prompt_hash IS NOT NULL")}  # fmt: skip
    assert step_hashes == {h for h, _ in rows.values()}

    # A second run adds no rows.
    state = document(DOC)
    model_client.replies[PRIMARY] = [classified(DOC), good_parse(DOC)]  # page reused
    pipeline_graph.run_document(state)
    assert one(db, "SELECT count(*) FROM prompts") == (3,)


def test_a_prompt_given_only_by_its_hash_stores_no_text(db, document):
    @traced("probe", input_of=lambda s: {})
    def probe(state):
        from observability import current_step

        current_step().set_prompt("p", hash=prompt_hash("unseen"))
        return {}

    with start_run(document(DOC)["document_id"], "test-1") as run:
        probe({})
        run.finish("failed")
    assert one(db, "SELECT count(*) FROM prompts") == (0,)


def _all(db, sql, *params):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# -------------------------------------------------------------------
# Replay
# -------------------------------------------------------------------
def test_a_transcribe_step_replayed_with_the_same_reply_is_the_same(ran, model_client):
    model_client.replies[PRIMARY] = [page_text(DOC)]
    r = replay(ran["transcribe"])
    assert r.same and r.diff() == []
    assert (r.node, r.page, r.prompt_name, r.prompt_current) == (
        "transcribe", 1, "extract_page", True,
    )  # fmt: skip
    assert r.models == [PRIMARY] and r.stored_model == PRIMARY
    # The call is the step's: the page image and the stored prompt.
    (call,) = model_client.calls[-1:]
    assert call["messages"][0]["content"] == prompt("extract_page")
    assert call["options"]["num_predict"] == 4096


def test_a_changed_reading_shows_as_a_diff(ran, model_client):
    lines = page_text(DOC).split("\n")
    changed = [*lines[:-1], lines[-1] + " (misread)"]
    model_client.replies[PRIMARY] = ["\n".join(changed)]
    r = replay(ran["transcribe"])
    assert not r.same
    diff = r.diff()
    assert f"-{lines[-1]}" in diff and f"+{lines[-1]} (misread)" in diff
    assert "replayed on " + PRIMARY in replay_mod.report(r)


def test_a_capped_replay_says_so(ran, model_client):
    model_client.replies[PRIMARY] = [("cut", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [("cut", "length")]
    r = replay(ran["transcribe"])
    assert r.replayed == CAPPED_TEXT and not r.same


def test_a_parse_step_is_replayed_from_the_runs_transcription(ran, model_client):
    reply = json.loads(good_parse(DOC))
    model_client.replies[PRIMARY] = [json.dumps(reply)]
    r = replay(ran["parse"])
    assert r.same
    call = model_client.calls[-1]
    assert call["messages"][0]["content"] == prompt("parse_prescription")
    assert call["messages"][1]["content"].startswith("=== Page 1 ===\n1| ")
    assert call["format"] is not None

    first = reply["medicines"][0]
    first["name"] = first["name"] + "X"
    model_client.replies[PRIMARY] = [json.dumps(reply)]
    r = replay(ran["parse"])
    assert [x for x in r.diff() if x.startswith("+ ")] == [
        f'+      "name": {json.dumps(first["name"])},'
    ]


def test_a_classify_step_compares_the_label(ran, model_client):
    model_client.replies[PRIMARY] = [classified(DOC)]
    assert replay(ran["classify"]).same
    model_client.replies[PRIMARY] = [json.dumps({"document_type": "lab_report"})]
    r = replay(ran["classify"])
    assert (r.stored, r.replayed) == (DOCS[DOC]["type"], "lab_report")


@pytest.mark.parametrize("node", ["open", "validate", "render", "finish"])
def test_a_step_without_a_model_call_is_refused(ran, node):
    with pytest.raises(ReplayError, match=f"a {node} step can't be replayed"):
        replay(ran[node])


def test_an_unknown_step_is_refused(db):
    with pytest.raises(ReplayError, match="no pipeline step"):
        replay("00000000-0000-0000-0000-000000000000")
    with pytest.raises(ReplayError, match="not a step id"):
        replay("nope")


def test_a_step_whose_prompt_text_was_not_stored_is_refused(db, ran):
    with db.cursor() as cur:
        cur.execute("DELETE FROM prompts WHERE name = 'extract_page'")
    with pytest.raises(ReplayError, match="prompt text was not stored"):
        replay(ran["transcribe"])


def test_a_page_whose_file_changed_is_refused(db, ran):
    other = str(DATASET_DIR / DOCS["d01"]["file"])
    with db.cursor() as cur:
        cur.execute("UPDATE documents SET storage_path = %s", (other,))
    with pytest.raises(ReplayError, match="page 1 of the file has changed"):
        replay(ran["transcribe"])


def test_an_edited_prompt_file_is_noted(db, ran, model_client):
    with db.cursor() as cur:  # as if the step had run with an older prompt
        cur.execute(
            "INSERT INTO prompts (hash, name, text) VALUES (%s, 'extract_page', 'old')",
            (prompt_hash("old"),),
        )
        cur.execute(
            "UPDATE pipeline_steps SET prompt_hash = %s WHERE id = %s",
            (prompt_hash("old"), ran["transcribe"]),
        )
    model_client.replies[PRIMARY] = [page_text(DOC)]
    r = replay(ran["transcribe"])
    assert not r.prompt_current
    assert model_client.calls[-1]["messages"][0]["content"] == "old"
    assert "(changed in the prompt files since)" in replay_mod.report(r)


def test_the_command_exits_by_whether_the_reply_changed(ran, model_client, capsys):
    model_client.replies[PRIMARY] = [page_text(DOC)]
    assert replay_mod.main([ran["transcribe"]]) == 0
    assert "the reply is the same" in capsys.readouterr().out
    model_client.replies[PRIMARY] = ["something else"]
    assert replay_mod.main([ran["transcribe"]]) == 1
    assert replay_mod.main([ran["finish"]]) == 2
    assert "can't replay" in capsys.readouterr().err
