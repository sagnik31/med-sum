"""
db/schema.sql: the pipeline tables (section 8.4) and the record table
(section 8.1) hold what they should and refuse what they shouldn't.
"""

import json

import psycopg
import pytest

from dataset_records import key
from db_rows import add_account


def one(db, sql, *params):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def run(db, sql, *params):
    with db.cursor() as cur:
        cur.execute(sql, params)


@pytest.fixture
def document(db):
    user = add_account(db)
    (doc,) = one(db, "INSERT INTO documents (user_id, original_name, content_type, "
                     "storage_path) VALUES (%s, 'a.pdf', 'application/pdf', '/x/a.pdf') "
                     "RETURNING id", user)  # fmt: skip
    return doc


def new_run(db, document) -> str:
    (run_id,) = one(db, "INSERT INTO pipeline_runs (document_id, graph_version) "
                        "VALUES (%s, 'v1') RETURNING id", document)  # fmt: skip
    return run_id


def new_step(db, run_id, seq, **cols) -> str:
    cols = {"run_id": run_id, "seq": seq, "node": "parse", **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    (step,) = one(db, f"INSERT INTO pipeline_steps ({names}) VALUES ({marks}) RETURNING id",
                  *cols.values())  # fmt: skip
    return step


def refused(db, sql, *params):
    with pytest.raises(psycopg.errors.IntegrityError):
        run(db, sql, *params)


def columns(db, table) -> set[str]:
    with db.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s", (table,))  # fmt: skip
        return {name for (name,) in cur.fetchall()}


def test_the_unused_columns_are_gone(db):
    assert not {"status", "processing_status", "last_error"} & columns(db, "documents")
    assert not {"summary_text", "details_json", "generated_at"} & columns(
        db, "insights"
    )


def test_a_run_starts_open_and_ends_in_one_of_the_end_states(db, document):
    run_id = new_run(db, document)
    trace, end_state = one(db, "SELECT trace_id, end_state FROM pipeline_runs WHERE id = %s",
                           run_id)  # fmt: skip

    assert len(trace) == 32 and int(trace, 16) >= 0  # an OpenTelemetry trace id
    assert end_state is None
    run(db, "UPDATE pipeline_runs SET end_state = 'needs_review', ended_at = now() "
            "WHERE id = %s", run_id)  # fmt: skip
    refused(
        db, "UPDATE pipeline_runs SET end_state = 'completed' WHERE id = %s", run_id
    )
    refused(db, "UPDATE pipeline_runs SET ended_at = NULL WHERE id = %s", run_id)


def test_steps_are_ordered_and_consistent(db, document):
    run_id = new_run(db, document)
    parse = new_step(db, run_id, 1)
    new_step(db, run_id, 2, node="validate", parent_step_id=parse,
             findings=json.dumps([{"validator": "source_text"}]))  # fmt: skip

    refused(
        db, "INSERT INTO pipeline_steps (run_id, seq, node) VALUES (%s, 2, 'x')", run_id
    )
    refused(
        db,
        "INSERT INTO pipeline_steps (run_id, seq, node, status) "
        "VALUES (%s, 3, 'x', 'ok')",
        run_id,
    )  # ended without an end time
    refused(
        db,
        "INSERT INTO pipeline_steps (run_id, seq, node, model_role) "
        "VALUES (%s, 3, 'x', 'primary')",
        run_id,
    )  # a role without a model
    refused(db, "INSERT INTO pipeline_steps (run_id, seq, node, status, ended_at) "
                "VALUES (%s, 3, 'x', 'skipped', now())", run_id)  # fmt: skip
    (findings,) = one(db, "SELECT findings FROM pipeline_steps WHERE seq = 2")
    assert findings == [{"validator": "source_text"}]
    (default,) = one(db, "SELECT findings FROM pipeline_steps WHERE seq = 1")
    assert default == []


def test_a_record_carries_its_schema_version(db, document):
    record = key("d01").model_dump_json()
    run(db, "INSERT INTO document_records (document_id, schema_version, record) "
            "VALUES (%s, 1, %s)", document, record)  # fmt: skip
    (stored,) = one(
        db, "SELECT record FROM document_records WHERE document_id = %s", document
    )

    assert stored["observations"][0]["printed"]["test_name"] == "Haemoglobin"
    refused(db, "UPDATE document_records SET schema_version = 2")  # the JSON says 1
    refused(db, "UPDATE document_records SET record = '[]'")


def test_deleting_a_document_deletes_its_runs_steps_and_record(db, document):
    run_id = new_run(db, document)
    new_step(db, run_id, 1)
    run(db, "INSERT INTO document_records (document_id, schema_version, record, run_id) "
            "VALUES (%s, 1, '{\"schema_version\": 1}', %s)", document, run_id)  # fmt: skip

    run(db, "DELETE FROM documents WHERE id = %s", document)
    for table in ("pipeline_runs", "pipeline_steps", "document_records"):
        assert one(db, f"SELECT count(*) FROM {table}") == (0,), table


def test_a_record_outlives_the_run_that_made_it(db, document):
    run_id = new_run(db, document)
    run(db, "INSERT INTO document_records (document_id, schema_version, record, run_id) "
            "VALUES (%s, 1, '{\"schema_version\": 1}', %s)", document, run_id)  # fmt: skip

    run(db, "DELETE FROM pipeline_runs WHERE id = %s", run_id)
    assert one(db, "SELECT run_id FROM document_records") == (None,)


def test_one_job_row_per_document(db, document):
    run(
        db,
        "INSERT INTO insights (document_id, status) VALUES (%s, 'processing')",
        document,
    )

    refused(
        db, "INSERT INTO insights (document_id, status) VALUES (%s, 'failed')", document
    )
