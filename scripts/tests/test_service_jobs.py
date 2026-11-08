"""
Document and patient-summary jobs against a throwaway database: claiming,
caching, failure recording. Extraction and generation are stubbed.
"""

import pytest
from fastapi.testclient import TestClient

import extract_report_slm
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


def add_insight(db, document_id, status, age="0 minutes", error=None):
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO insights (document_id, user_id, status, error_message, updated_at)
            SELECT id, user_id, %s, %s, NOW() - %s::interval FROM documents WHERE id = %s
            """,
            (status, error, age, document_id),
        )


def insight_row(db, document_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, html_insights, error_message FROM insights WHERE document_id = %s",
            (document_id,),
        )
        return cur.fetchone()


def cached_markdown(db, document_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT extracted_markdown FROM documents WHERE id = %s", (document_id,)
        )
        return cur.fetchone()[0]


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
    """Stub extraction and HTML generation; record what each received."""
    calls = {"extract": [], "generate": []}

    def fake_extract(path, on_page=None):
        calls["extract"].append(path)
        if on_page:
            on_page()
        return "| Haemoglobin | 13.2 g/dL |"

    def fake_generate(markdown, prompt=None):
        calls["generate"].append({"markdown": markdown, "prompt": prompt})
        return "<html><body><h2>Summary</h2></body></html>"

    monkeypatch.setattr(extract_report_slm, "extract_markdown_from_file", fake_extract)
    monkeypatch.setattr(generate_insights_txt, "generate_insights_html", fake_generate)
    return calls


# -------------------------------------------------------------------
# Document pipeline
# -------------------------------------------------------------------
def test_a_new_document_is_extracted_cached_and_completed(db, upload, stages):
    doc = add_document(db, add_user(db), upload)

    service.run_insights_pipeline(doc)

    assert stages["extract"] == [str(upload)]
    assert stages["generate"][0]["markdown"] == "| Haemoglobin | 13.2 g/dL |"
    assert cached_markdown(db, doc) == "| Haemoglobin | 13.2 g/dL |"
    status, html, error = insight_row(db, doc)
    assert status == "completed"
    assert "<h2>Summary</h2>" in html
    assert error is None


def test_cached_markdown_skips_extraction(db, upload, stages):
    doc = add_document(db, add_user(db), upload, markdown="cached text")

    service.run_insights_pipeline(doc)

    assert stages["extract"] == []
    assert stages["generate"][0]["markdown"] == "cached text"
    assert insight_row(db, doc)[0] == "completed"


def test_a_completed_document_is_not_processed_again(db, upload, stages):
    doc = add_document(db, add_user(db), upload)
    add_insight(db, doc, "completed")

    service.run_insights_pipeline(doc)

    assert stages["extract"] == [] and stages["generate"] == []
    assert insight_row(db, doc)[0] == "completed"


def test_a_live_job_is_not_taken_over(db, upload, stages):
    doc = add_document(db, add_user(db), upload)
    add_insight(db, doc, "processing", age="29 minutes")

    service.run_insights_pipeline(doc)

    assert stages["extract"] == []
    assert insight_row(db, doc)[0] == "processing"


def test_a_stale_job_is_taken_over(db, upload, stages):
    doc = add_document(db, add_user(db), upload)
    add_insight(db, doc, "processing", age="31 minutes")

    service.run_insights_pipeline(doc)

    assert insight_row(db, doc)[0] == "completed"


def test_a_failed_job_is_retried_and_its_error_cleared(db, upload, stages):
    doc = add_document(db, add_user(db), upload)
    add_insight(db, doc, "failed", error="model down")

    service.run_insights_pipeline(doc)

    status, _, error = insight_row(db, doc)
    assert status == "completed"
    assert error is None


def test_an_unknown_document_is_ignored(db, stages):
    service.run_insights_pipeline("00000000-0000-0000-0000-000000000000")

    assert stages["extract"] == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM insights")
        assert cur.fetchone()[0] == 0


def test_a_missing_file_fails_the_job(db, tmp_path, stages):
    doc = add_document(db, add_user(db), tmp_path / "deleted.pdf")

    service.run_insights_pipeline(doc)

    status, html, error = insight_row(db, doc)
    assert (status, html, error) == ("failed", None, "Uploaded file not found on disk.")
    assert stages["extract"] == []


def test_a_model_failure_is_recorded_and_nothing_is_saved(
    db, upload, stages, monkeypatch
):
    def fail(markdown, prompt=None):
        raise RuntimeError("No model produced complete output.")

    monkeypatch.setattr(generate_insights_txt, "generate_insights_html", fail)
    doc = add_document(db, add_user(db), upload)

    service.run_insights_pipeline(doc)

    assert insight_row(db, doc) == (
        "failed",
        None,
        "No model produced complete output.",
    )
    # The transcription was saved before generation failed, so a retry
    # does not repeat the slow vision step.
    assert cached_markdown(db, doc) == "| Haemoglobin | 13.2 g/dL |"


def test_an_error_without_a_message_records_its_type(db, upload, monkeypatch):
    def fail(path, on_page=None):
        raise TimeoutError()

    monkeypatch.setattr(extract_report_slm, "extract_markdown_from_file", fail)
    doc = add_document(db, add_user(db), upload)

    service.run_insights_pipeline(doc)

    assert insight_row(db, doc)[0::2] == ("failed", "TimeoutError")


def test_the_heartbeat_only_touches_a_processing_job(db, upload):
    doc = add_document(db, add_user(db), upload)
    add_insight(db, doc, "completed", age="2 hours")
    conn = service.get_db_connection()
    try:
        service.touch_insight(conn, doc)
    finally:
        conn.close()

    with db.cursor() as cur:
        cur.execute(
            "SELECT updated_at < NOW() - interval '1 hour' FROM insights WHERE document_id = %s",
            (doc,),
        )
        assert cur.fetchone()[0] is True


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
