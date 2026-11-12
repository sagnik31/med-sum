"""
Patient-summary jobs against a throwaway database: claiming and failure
recording, with generation stubbed. Document jobs are in
test_document_jobs.py.
"""

import pytest
from fastapi.testclient import TestClient

from config import PATIENT_SUMMARY_PROMPT_FILE
import generate_insights_txt
import insights_service as service
from db_rows import add_account

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


@pytest.fixture
def stages(monkeypatch):
    """Stub HTML generation; record what it received."""
    calls = {"generate": []}

    def fake_generate(markdown, prompt=None):
        calls["generate"].append({"markdown": markdown, "prompt": prompt})
        return "<html><body><h2>Summary</h2></body></html>"

    monkeypatch.setattr(generate_insights_txt, "generate_insights_html", fake_generate)
    return calls


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


def test_the_summary_covers_processed_documents_oldest_first(db, upload, stages):
    user = add_user(db)
    other = add_user(db, email="other@hospital.test")
    add_document(db, user, upload, "newer report", "2025-03-05 10:00+05:30")
    add_document(db, user, upload, "older report", "2024-11-20 10:00+05:30")
    add_document(db, user, upload, None)  # not processed yet
    add_document(db, other, upload, "another patient's report")

    service.run_patient_summary(user)

    sent = stages["generate"][0]
    assert sent["markdown"] == (
        "\n[REPORT 1 - 2024-11-20]\nolder report\n"
        "\n[REPORT 2 - 2025-03-05]\nnewer report\n"
        "\n[END OF REPORTS]\n"
    )
    with open(PATIENT_SUMMARY_PROMPT_FILE, encoding="utf-8") as f:
        assert sent["prompt"] == f.read()
    html, status, error = summary_row(db, user)
    assert status == "completed"
    assert "<h2>Summary</h2>" in html
    assert error is None


def test_a_summary_with_no_processed_documents_fails_with_a_reason(db, stages):
    user = add_user(db)

    service.run_patient_summary(user)

    html, status, error = summary_row(db, user)
    assert (html, status) == (None, "failed")
    assert error.startswith("No processed documents yet")
    assert stages["generate"] == []
