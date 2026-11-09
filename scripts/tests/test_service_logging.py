"""
The service's logs: JSON lines set up when the app starts, job events with
ids and sizes, and never document content or exception messages, which
go to the database only.
"""

import json
import logging

import httpx
import ollama
import pytest
from fastapi.testclient import TestClient

import config
import extract_report_slm
import generate_insights_txt
import insights_service as service
import llm
from db_rows import add_account

# Stands for document content: it must reach the database and never a log.
SECRET = "Haemoglobin 9.1 for Asha Verma"


def lines_of(log) -> list[dict]:
    return [json.loads(s) for s in log.getvalue().splitlines()]


def events_of(log) -> list[str]:
    return [line["event"] for line in lines_of(log) if "event" in line]


def line_with(log, event: str) -> dict:
    return next(line for line in lines_of(log) if line.get("event") == event)


# -------------------------------------------------------------------
# Start-up
# -------------------------------------------------------------------
def test_starting_the_app_sends_json_lines_to_the_log_file(
    tmp_path, monkeypatch, root_logger
):
    log_file = tmp_path / "logs" / "medsum.jsonl"
    monkeypatch.setattr(config, "LOG_FILE", str(log_file))
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", "t")

    with TestClient(service.app):  # runs the lifespan
        pass
    for handler in root_logger.handlers:
        handler.flush()

    entries = [json.loads(s) for s in log_file.read_text("utf-8").splitlines()]
    assert [e["event"] for e in entries] == ["service_started"]
    assert entries[0]["logger"] == "medsum.service"
    assert any(getattr(h, "_medsum_json", False) for h in root_logger.handlers)


def test_a_missing_internal_token_is_warned_about_at_start_up(
    tmp_path, monkeypatch, root_logger, json_log
):
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "medsum.jsonl"))
    monkeypatch.setattr(service, "INTERNAL_API_TOKEN", "")

    with TestClient(service.app):
        pass

    warning = line_with(json_log, "internal_token_missing")
    assert warning["level"] == "WARNING"


def test_importing_the_service_configures_no_logging(root_logger):
    # Tests and scripts import it; only a started app writes the log file.
    assert not any(getattr(h, "_medsum_json", False) for h in root_logger.handlers)


# -------------------------------------------------------------------
# Jobs
# -------------------------------------------------------------------
def add_document(db, path) -> str:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, %s, 'application/pdf', %s) RETURNING id",
            (add_account(db), f"{SECRET}.pdf", str(path)),
        )
        return str(cur.fetchone()[0])


@pytest.fixture
def upload(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7 fictional")
    return path


def test_a_document_job_logs_its_events_without_content(
    db, upload, monkeypatch, json_log
):
    monkeypatch.setattr(
        extract_report_slm, "extract_markdown_from_file", lambda path, on_page: SECRET
    )
    monkeypatch.setattr(
        generate_insights_txt,
        "generate_insights_html",
        lambda markdown: f"<p>{SECRET}</p>",
    )
    doc = add_document(db, upload)

    service.run_insights_pipeline(doc)

    assert events_of(json_log) == [
        "job_started",
        "transcription_started",
        "transcription_saved",
        "insights_generated",
        "job_completed",
        "job_finished",
    ]
    generated = line_with(json_log, "insights_generated")
    assert generated["document_id"] == doc
    assert generated["markdown_chars"] == len(SECRET)
    assert generated["html_chars"] == len(f"<p>{SECRET}</p>")
    log = json_log.getvalue()
    assert SECRET not in log and "Asha" not in log
    assert str(upload) not in log and upload.name not in log  # no file paths


def test_a_failed_document_job_logs_the_error_type_and_stores_the_message(
    db, upload, monkeypatch, json_log
):
    monkeypatch.setattr(
        extract_report_slm, "extract_markdown_from_file", lambda path, on_page: SECRET
    )

    def fail(markdown):
        raise ValueError(f"could not read {SECRET}")

    monkeypatch.setattr(generate_insights_txt, "generate_insights_html", fail)
    doc = add_document(db, upload)

    service.run_insights_pipeline(doc)

    failed = line_with(json_log, "job_failed")
    assert failed["level"] == "ERROR"
    assert failed["exc_type"] == "ValueError"
    assert failed["document_id"] == doc
    assert SECRET not in json_log.getvalue()
    with db.cursor() as cur:
        cur.execute("SELECT status, error_message FROM insights WHERE document_id = %s",
                    (doc,))  # fmt: skip
        assert cur.fetchone() == ("failed", f"could not read {SECRET}")


def test_a_transcription_that_cannot_be_saved_is_logged_and_the_job_goes_on(
    db, upload, monkeypatch, json_log
):
    # NUL can't be stored in a text column, so the cache write fails.
    monkeypatch.setattr(
        extract_report_slm,
        "extract_markdown_from_file",
        lambda path, on_page: f"{SECRET}\x00",
    )
    monkeypatch.setattr(
        generate_insights_txt, "generate_insights_html", lambda markdown: "<p>ok</p>"
    )
    doc = add_document(db, upload)

    service.run_insights_pipeline(doc)

    assert "transcription_not_saved" in events_of(json_log)
    assert events_of(json_log)[-2:] == ["job_completed", "job_finished"]
    assert SECRET not in json_log.getvalue()
    with db.cursor() as cur:
        cur.execute("SELECT status FROM insights WHERE document_id = %s", (doc,))
        assert cur.fetchone() == ("completed",)


def test_a_failed_patient_summary_logs_the_error_type_only(db, monkeypatch, json_log):
    user = add_account(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path,"
            " extracted_markdown) VALUES (%s, 'a.pdf', 'application/pdf', '/x', %s)",
            (user, SECRET),
        )

    def fail(markdown, prompt=None):
        raise RuntimeError(f"bad output about {SECRET}")

    monkeypatch.setattr(generate_insights_txt, "generate_insights_html", fail)

    service.run_patient_summary(user)

    assert events_of(json_log) == ["summary_started", "summary_failed"]
    assert line_with(json_log, "summary_failed")["user_id"] == user
    assert SECRET not in json_log.getvalue()


# -------------------------------------------------------------------
# Model calls
# -------------------------------------------------------------------
MESSAGES = [{"role": "user", "content": SECRET}]


@pytest.mark.parametrize(
    "error",
    [
        ollama.ResponseError(f"model choked on {SECRET}"),
        ConnectionError(f"refused while sending {SECRET}"),
        httpx.ReadTimeout(f"timed out reading {SECRET}"),
    ],
    ids=["response-error", "connection", "timeout"],
)
def test_a_model_failure_is_logged_by_type_and_the_fallback_noted(
    model_client, json_log, error
):
    model_client.replies[llm.PRIMARY_MODEL] = [error]
    model_client.replies[llm.FALLBACK_MODEL] = ["fine"]

    assert llm.chat(MESSAGES, max_tokens=10) == "fine"

    failed = line_with(json_log, "model_failed")
    assert failed["model"] == llm.PRIMARY_MODEL
    assert failed["model_role"] == "primary"
    assert failed["error_type"] == type(error).__name__
    fallback = line_with(json_log, "fallback_used")
    assert fallback["model"] == llm.FALLBACK_MODEL
    assert fallback["model_role"] == "fallback"
    assert SECRET not in json_log.getvalue()


def test_capped_output_is_logged_per_model(model_client, json_log):
    model_client.replies[llm.PRIMARY_MODEL] = [("partial", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [("partial", "length")]

    with pytest.raises(llm.ModelOutputError):
        llm.chat(MESSAGES, max_tokens=10)

    capped = [
        line for line in lines_of(json_log) if line.get("event") == "output_capped"
    ]
    assert [(c["model"], c["model_role"]) for c in capped] == [
        (llm.PRIMARY_MODEL, "primary"),
        (llm.FALLBACK_MODEL, "fallback"),
    ]
    assert all(c["level"] == logging.getLevelName(logging.WARNING) for c in capped)
