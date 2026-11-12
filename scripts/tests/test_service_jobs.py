"""
Patient-summary jobs against a throwaway database: claiming and failure
recording, with generation stubbed. Document jobs are in
test_document_jobs.py.
"""

import pytest
from fastapi.testclient import TestClient

from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

import insights_service as service
import llm
from dataset_records import key
from db_rows import add_account
from test_service_logging import line_with

TOKEN = "test-internal-token"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def add_user(db, email="clinician@hospital.test") -> str:
    return add_account(db, email)


def add_document(db, user_id, path, markdown=None, uploaded_at=None) -> str:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents
                (user_id, original_name, content_type, storage_path,
                 extracted_markdown, uploaded_at)
            VALUES (%s, 'report.pdf', 'application/pdf', %s, %s, COALESCE(%s, NOW()))
            RETURNING id
            """,
            (user_id, str(path), markdown, uploaded_at),
        )
        return str(cur.fetchone()[0])


def summary_row(db, user_id):
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT patient_insights, patient_insights_status, patient_insights_error
            FROM users WHERE id = %s
            """,
            (user_id,),
        )
        return cur.fetchone()


@pytest.fixture
def upload(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7 fictional")
    return path


def add_processed(db, user_id, doc_id, uploaded_at, status="verified") -> str:
    """A dataset document with its answer key stored as its record."""
    doc = add_document(db, user_id, f"/x/{doc_id}", "text", uploaded_at)
    record = key(doc_id).model_copy(deep=True)
    record.document.document_id = doc
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO insights (document_id, user_id, status) VALUES (%s, %s, %s)",
            (doc, user_id, status),
        )
        cur.execute(
            "INSERT INTO document_records (document_id, schema_version, record)"
            " VALUES (%s, 1, %s)",
            (doc, Jsonb(record.model_dump(mode="json"))),
        )
    return doc


# -------------------------------------------------------------------
# Patient summary
# -------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", TOKEN)
    return TestClient(service.app)


@pytest.fixture
def summary_jobs(monkeypatch):
    jobs = []
    monkeypatch.setattr(service, "run_patient_summary", jobs.append)
    return jobs


def request_summary(client, user_id):
    return client.post(
        "/internal/generate-user-insights",
        json={"user_id": user_id},
        headers={"X-Internal-Token": TOKEN},
    )


def test_a_summary_request_claims_the_job(db, client, summary_jobs):
    user = add_user(db)

    response = request_summary(client, user)

    assert response.json() == {"status": "accepted"}
    assert summary_jobs == [user]
    assert summary_row(db, user)[1] == "processing"


def test_a_second_request_during_a_live_summary_is_not_queued(db, client, summary_jobs):
    user = add_user(db)
    request_summary(client, user)

    response = request_summary(client, user)

    assert response.json() == {"status": "already_processing"}
    assert summary_jobs == [user]


def test_a_stale_summary_job_is_taken_over(db, client, summary_jobs):
    user = add_user(db)
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE users SET patient_insights_status = 'processing',
                             patient_insights_updated_at = NOW() - interval '31 minutes'
            WHERE id = %s
            """,
            (user,),
        )

    assert request_summary(client, user).json() == {"status": "accepted"}


def test_the_summary_is_built_from_records_in_document_date_order(db, model_client):
    user = add_user(db)
    other = add_user(db, email="other@hospital.test")
    # d04 (2025-01-10) is uploaded before the 2024 documents.
    add_processed(db, user, "d04", "2024-01-01 10:00+05:30")
    add_processed(db, user, "d01", "2025-06-01 10:00+05:30")
    add_processed(db, user, "d02", "2025-06-02 10:00+05:30", status="needs_review")
    add_document(db, user, "/x/new", None)  # not processed yet
    add_processed(db, other, "d09", "2025-06-03 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = ["The documents cover lab reports."]

    service.run_patient_summary(user)

    html, status, error = summary_row(db, user)
    assert (status, error) == ("completed", None)
    soup = BeautifulSoup(html, "html.parser")
    timeline = soup.select('[data-section="timeline"] tr')
    assert [r.select("td")[1].get_text() for r in timeline] == [
        "2024-06-14", "2024-06-20", "2025-01-10",
    ]  # fmt: skip
    assert soup.select_one('[data-warning="not_included"]') is not None
    assert "Fatima" not in html  # another user's document
    # The model read the computed facts, not the transcriptions.
    [call] = model_client.calls
    assert call["messages"][1]["content"].startswith("Documents: 3 (")
    assert soup.select_one('[data-overview="shown"]').get_text() == (
        "The documents cover lab reports."
    )


def test_a_rejected_overview_leaves_the_summary_complete_without_it(
    db, model_client, json_log
):
    user = add_user(db)
    add_processed(db, user, "d06", "2025-02-04 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = [
        "Creatinine suggests acute kidney injury.",
        "Creatinine suggests kidney injury.",  # the retry fails too
    ]

    service.run_patient_summary(user)

    html, status, _ = summary_row(db, user)
    assert status == "completed"
    assert "kidney injury" not in html
    assert 'data-overview="rejected"' in html
    left_out = line_with(json_log, "summary_overview_left_out")
    assert (left_out["overview"], left_out["unknown_words"]) == ("rejected", 3)
    assert left_out["attempts"] == 2


def test_a_summary_with_no_processed_documents_fails_with_a_reason(db, model_client):
    user = add_user(db)

    service.run_patient_summary(user)

    html, status, error = summary_row(db, user)
    assert (html, status) == (None, "failed")
    assert error.startswith("No processed documents yet")
    assert model_client.calls == []
