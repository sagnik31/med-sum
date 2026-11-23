"""
Patient-summary jobs against a throwaway database: claiming and failure
recording, with generation stubbed. Document jobs are in
test_document_jobs.py.
"""

import time

import pytest
from fastapi.testclient import TestClient

from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

import document_jobs as jobs
import insights_service as service
import llm
from dataset_records import key
from summary.build import load_inputs
from db_rows import add_patient
from test_service_logging import line_with

TOKEN = "test-internal-token"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def add_user(db, email="clinician@hospital.test") -> str:
    """A patient record (created by the account with that email)."""
    return add_patient(db, email)


def add_document(db, patient_id, path, markdown=None, uploaded_at=None) -> str:
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents
                (patient_id, original_name, content_type, storage_path,
                 extracted_markdown, uploaded_at)
            VALUES (%s, 'report.pdf', 'application/pdf', %s, %s, COALESCE(%s, NOW()))
            RETURNING id
            """,
            (patient_id, str(path), markdown, uploaded_at),
        )
        return str(cur.fetchone()[0])


def summary_row(db, patient_id):
    """The patient's summary (html, status, error); all None without a row."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT html, status, error FROM patient_summaries WHERE patient_id = %s",
            (patient_id,),
        )
        return cur.fetchone() or (None, None, None)


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
            "INSERT INTO insights (document_id, status) VALUES (%s, %s)",
            (doc, status),
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
    monkeypatch.setattr(
        service, "run_patient_summary", lambda user, claim: jobs.append(user)
    )
    return jobs


def run_summary(user_id):
    """Claim the patient's summary, as the endpoint does, and build it."""
    claim = service.claim_patient_summary(user_id)
    assert claim is not None
    service.run_patient_summary(user_id, claim)


def request_summary(client, user_id):
    return client.post(
        "/internal/generate-patient-summary",
        json={"patient_id": user_id},
        headers={"X-Internal-Token": TOKEN},
    )


def test_a_summary_request_claims_the_job(db, client, summary_jobs):
    user = add_user(db)

    response = request_summary(client, user)

    assert response.json() == {"status": "accepted"}
    assert summary_jobs == [user]
    assert summary_row(db, user)[1] == "processing"


def rerun_requested(db, patient_id) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "SELECT rerun_requested FROM patient_summaries WHERE patient_id = %s",
            (patient_id,),
        )
        return cur.fetchone()[0]


def test_a_second_request_during_a_live_summary_is_queued_behind_it(
    db, client, summary_jobs
):
    user = add_user(db)
    request_summary(client, user)

    response = request_summary(client, user)

    assert response.json() == {"status": "queued"}
    assert summary_jobs == [user]  # no second job: the live run builds again
    assert rerun_requested(db, user)


def test_a_stale_summary_job_is_taken_over(db, client, summary_jobs):
    user = add_user(db)
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE patient_summaries SET status = 'processing',
                             updated_at = NOW() - interval '31 minutes'
            WHERE patient_id = %s
            """,
            (user,),
        )

    assert request_summary(client, user).json() == {"status": "accepted"}


# An overview that names the medicines on d04, the latest prescription,
# which the overview check requires.
COVERS_D04 = (
    "The documents cover lab reports. The latest prescription lists"
    " Glycomet GP, Telma, Orofer XT, Pan and Dolo."
)


def test_the_summary_is_built_from_records_in_document_date_order(db, model_client):
    user = add_user(db)
    other = add_user(db, email="other@hospital.test")
    # d04 (2025-01-10) is uploaded before the 2024 documents.
    add_processed(db, user, "d04", "2024-01-01 10:00+05:30")
    add_processed(db, user, "d01", "2025-06-01 10:00+05:30")
    add_processed(db, user, "d02", "2025-06-02 10:00+05:30", status="needs_review")
    add_document(db, user, "/x/new", None)  # not processed yet
    add_processed(db, other, "d09", "2025-06-03 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = [COVERS_D04]

    run_summary(user)

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
    assert soup.select_one('[data-overview="shown"]').get_text() == COVERS_D04


def test_a_file_uploaded_twice_counts_once(db, model_client):
    user = add_user(db)
    first = add_processed(db, user, "d01", "2025-06-01 10:00+05:30")
    again = add_processed(db, user, "d01", "2025-06-05 10:00+05:30")
    other = add_processed(db, user, "d04", "2025-06-06 10:00+05:30")
    with db.cursor() as cur:
        cur.execute("UPDATE documents SET sha256 = repeat('a', 64) WHERE id IN (%s, %s)",
                    (first, again))  # fmt: skip
        cur.execute("UPDATE documents SET sha256 = repeat('b', 64) WHERE id = %s",
                    (other,))  # fmt: skip
        inputs = load_inputs(db, user)
    assert [i.document_id for i in inputs] == [first, other]


@pytest.mark.parametrize("first_status", ["failed", "processing"])
def test_a_finished_copy_stands_in_for_one_that_did_not_finish(
    db, model_client, first_status
):
    user = add_user(db)
    first = add_processed(db, user, "d01", "2025-06-01 10:00+05:30", first_status)
    again = add_processed(db, user, "d01", "2025-06-05 10:00+05:30")
    with db.cursor() as cur:
        cur.execute("UPDATE documents SET sha256 = repeat('a', 64) WHERE id IN (%s, %s)",
                    (first, again))  # fmt: skip
        inputs = load_inputs(db, user)
    assert [(i.document_id, i.status) for i in inputs] == [(again, "verified")]


def test_with_no_finished_copy_the_earliest_stands_in(db, model_client):
    user = add_user(db)
    first = add_processed(db, user, "d01", "2025-06-01 10:00+05:30", "failed")
    again = add_processed(db, user, "d01", "2025-06-05 10:00+05:30", "failed")
    with db.cursor() as cur:
        cur.execute("UPDATE documents SET sha256 = repeat('a', 64) WHERE id IN (%s, %s)",
                    (first, again))  # fmt: skip
        inputs = load_inputs(db, user)
    assert [i.document_id for i in inputs] == [first]


def test_a_deleted_document_is_left_out_and_its_copy_stands_in(db, model_client):
    user = add_user(db)
    first = add_processed(db, user, "d01", "2025-06-01 10:00+05:30")
    again = add_processed(db, user, "d01", "2025-06-05 10:00+05:30")
    gone = add_processed(db, user, "d04", "2025-06-06 10:00+05:30")
    with db.cursor() as cur:
        cur.execute("UPDATE documents SET sha256 = repeat('a', 64) WHERE id IN (%s, %s)",
                    (first, again))  # fmt: skip
        # Deleting is soft: the rows stay, marked.
        cur.execute("UPDATE documents SET deleted_at = NOW() WHERE id IN (%s, %s)",
                    (first, gone))  # fmt: skip
        inputs = load_inputs(db, user)
    assert [i.document_id for i in inputs] == [again]


def test_the_summary_covers_one_patient_only(db, model_client):
    meera, other = add_user(db), add_user(db)
    mine = add_processed(db, meera, "d01", "2025-06-01 10:00+05:30")
    add_processed(db, other, "d04", "2025-06-06 10:00+05:30")
    inputs = load_inputs(db, meera)
    assert [i.document_id for i in inputs] == [mine]


def test_no_summary_is_claimed_for_an_unknown_patient(db):
    assert service.claim_patient_summary("00000000-0000-4000-8000-000000000000") is None


def test_a_rejected_overview_leaves_the_summary_complete_without_it(
    db, model_client, json_log
):
    user = add_user(db)
    add_processed(db, user, "d06", "2025-02-04 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = [
        "Creatinine suggests acute kidney injury.",
        "Creatinine suggests kidney injury.",  # the retry fails too
    ]

    run_summary(user)

    html, status, _ = summary_row(db, user)
    assert status == "completed"
    assert "kidney injury" not in html
    assert 'data-overview="rejected"' in html
    left_out = line_with(json_log, "summary_overview_left_out")
    assert (left_out["overview"], left_out["unknown_words"]) == ("rejected", 3)
    assert (left_out["missing_facts"], left_out["attempts"]) == (0, 2)


def test_a_summary_with_no_processed_documents_fails_with_a_reason(db, model_client):
    user = add_user(db)

    run_summary(user)

    html, status, error = summary_row(db, user)
    assert (html, status) == (None, "failed")
    assert error.startswith("No processed documents yet")
    assert model_client.calls == []


def take_over(db, user_id):
    """Another run claims the summary, as after STALE_PROCESSING_AFTER."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE patient_summaries SET updated_at ="
            " updated_at - interval '31 minutes' WHERE patient_id = %s",
            (user_id,),
        )
    newer = service.claim_patient_summary(user_id)
    assert newer is not None
    return newer


def test_a_run_that_lost_its_claim_does_not_write_its_summary(
    db, model_client, monkeypatch, json_log
):
    user = add_user(db)
    add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]
    old = service.claim_patient_summary(user)
    real_build = service_build()

    def slow_build(inputs, **kwargs):
        take_over(db, user)  # the run went quiet and was taken over meanwhile
        return real_build(inputs, **kwargs)

    monkeypatch.setattr("summary.build.build_summary", slow_build)

    service.run_patient_summary(user, old)

    assert summary_row(db, user) == (None, "processing", None)
    assert line_with(json_log, "summary_claim_lost")["patient_id"] == user


def test_a_live_run_s_heartbeat_keeps_its_claim_past_the_stale_time(
    db, model_client, monkeypatch
):
    # ADR 0006: a long build is not taken over while its heartbeat goes on.
    user = add_user(db)
    add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."] * 2
    monkeypatch.setattr(service, "STALE_PROCESSING_AFTER", "300 milliseconds")
    monkeypatch.setattr(service, "SUMMARY_HEARTBEAT_S", 0.05)
    claim = service.claim_patient_summary(user)
    real_build = service_build()
    asked = []

    def slow_build(inputs, **kwargs):
        if not asked:
            time.sleep(0.8)  # longer than the stale time, heartbeat running
            asked.append(service.claim_patient_summary(user))
        return real_build(inputs, **kwargs)

    monkeypatch.setattr("summary.build.build_summary", slow_build)

    service.run_patient_summary(user, claim)

    assert asked == [None]  # not taken over: queued behind the live run
    html, status, error = summary_row(db, user)
    assert status == "completed" and html and error is None


def test_a_heartbeat_stops_once_the_claim_is_taken_over(db):
    user = add_user(db)
    claim = service.claim_patient_summary(user)
    newer = take_over(db, user)
    with service.SummaryHeartbeat(user, claim, every=0.02) as beat:
        beat._thread.join(timeout=2)
        assert not beat._thread.is_alive()  # stopped by itself
    assert beat.claim == claim  # never moved the newer run's claim
    with db.cursor() as cur:
        cur.execute(
            "SELECT updated_at FROM patient_summaries WHERE patient_id = %s", (user,)
        )
        assert cur.fetchone()[0] == newer


def test_a_run_that_lost_its_claim_does_not_mark_the_newer_one_failed(
    db, model_client, json_log
):
    user = add_user(db)  # no processed documents: the run fails
    old = service.claim_patient_summary(user)
    take_over(db, user)

    service.run_patient_summary(user, old)

    assert summary_row(db, user) == (None, "processing", None)
    assert line_with(json_log, "summary_claim_lost")["patient_id"] == user


def test_the_run_holding_the_claim_still_writes_after_a_late_one_gave_up(
    db, model_client
):
    user = add_user(db)
    add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."] * 2
    old = service.claim_patient_summary(user)
    newer = take_over(db, user)
    service.run_patient_summary(user, old)  # refused

    service.run_patient_summary(user, newer)

    html, status, _ = summary_row(db, user)
    assert status == "completed" and "TSH" in html


def test_a_request_during_a_run_builds_the_summary_again_from_fresh_inputs(
    db, model_client, monkeypatch, json_log
):
    user = add_user(db)
    add_processed(db, user, "d01", "2025-06-01 10:00+05:30")
    gone = add_processed(db, user, "d04", "2025-06-06 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = [
        COVERS_D04,  # with d04's prescription
        "The documents cover lab reports.",  # d04 deleted meanwhile
    ]
    real_build = service_build()
    builds = []

    def build_with_a_delete_meanwhile(inputs, **kwargs):
        builds.append([i.document_id for i in inputs])
        if len(builds) == 1:
            # The inputs are read; a document is deleted and Go asks for a
            # rebuild while this run is still building.
            with db.cursor() as cur:
                cur.execute("UPDATE documents SET deleted_at = NOW() WHERE id = %s",
                            (gone,))  # fmt: skip
            assert service.claim_patient_summary(user) is None
        return real_build(inputs, **kwargs)

    monkeypatch.setattr("summary.build.build_summary", build_with_a_delete_meanwhile)

    run_summary(user)

    assert len(builds) == 2
    assert gone in builds[0] and gone not in builds[1]
    html, status, error = summary_row(db, user)
    assert (status, error) == ("completed", None)
    assert model_client.calls[-1]["messages"][1]["content"].startswith("Documents: 1 (")
    assert not rerun_requested(db, user)
    assert line_with(json_log, "summary_rebuild")["patient_id"] == user


def test_a_request_during_a_failing_run_builds_the_summary_again(
    db, model_client, monkeypatch
):
    user = add_user(db)  # no processed documents yet: the first build fails
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]
    real_load = load_inputs
    loads = []

    def load_then_a_document_lands(conn, patient_id):
        inputs = real_load(conn, patient_id)
        loads.append(len(inputs))
        if len(loads) == 1:
            add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
            assert service.claim_patient_summary(user) is None
        return inputs

    monkeypatch.setattr("summary.build.load_inputs", load_then_a_document_lands)

    run_summary(user)

    assert loads == [0, 1]
    html, status, error = summary_row(db, user)
    assert (status, error) == ("completed", None) and "TSH" in html


def test_a_finished_run_is_not_built_again(db, model_client, monkeypatch):
    user = add_user(db)
    add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]
    real_build = service_build()
    builds = []
    monkeypatch.setattr(
        "summary.build.build_summary",
        lambda inputs, **kw: builds.append(1) or real_build(inputs, **kw),
    )

    run_summary(user)

    assert builds == [1]
    assert summary_row(db, user)[1] == "completed"
    assert not rerun_requested(db, user)


def test_a_takeover_clears_a_request_queued_behind_the_stale_run(db):
    user = add_user(db)
    service.claim_patient_summary(user)
    assert service.claim_patient_summary(user) is None  # queued behind it

    take_over(db, user)  # the new run reads fresh inputs

    assert not rerun_requested(db, user)


def service_build():
    from summary import build

    return build.build_summary


# -------------------------------------------------------------------
# The summary rebuilt after a document
# -------------------------------------------------------------------
def set_summary(db, user_id, status, html="old summary", minutes_ago=0):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO patient_summaries (patient_id, html, status, updated_at)"
            " VALUES (%s, %s, %s, NOW() - make_interval(mins => %s))"
            " ON CONFLICT (patient_id) DO UPDATE SET html = EXCLUDED.html,"
            " status = EXCLUDED.status, updated_at = EXCLUDED.updated_at",
            (user_id, html, status, minutes_ago),
        )


def finished_job(doc) -> jobs.Job:
    return jobs.Job(id="00000000-0000-0000-0000-000000000001", document_id=doc,
                    attempts=1, run_id=None)  # fmt: skip


@pytest.mark.parametrize("before", ["completed", "failed"])
def test_a_finished_document_rebuilds_its_uploaders_summary(db, model_client, before):
    user = add_user(db)
    doc = add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    set_summary(db, user, before)
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]

    service.refresh_after_document(finished_job(doc))

    html, status, _ = summary_row(db, user)
    assert status == "completed"
    assert html != "old summary" and "TSH" in html
    assert len(model_client.calls) == 1


def test_no_summary_is_built_for_a_user_who_never_asked_for_one(db, model_client):
    user = add_user(db)
    doc = add_processed(db, user, "d09", "2024-09-06 10:00+05:30")

    service.refresh_after_document(finished_job(doc))

    assert summary_row(db, user) == (None, None, None)
    assert model_client.calls == []


def test_the_summary_waits_for_the_users_last_open_document(db, model_client):
    user = add_user(db)
    doc = add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    other = add_document(db, user, "/x/other")
    jobs.enqueue(db, other)
    set_summary(db, user, "completed")

    service.refresh_after_document(finished_job(doc))

    assert summary_row(db, user)[:2] == ("old summary", "completed")
    assert model_client.calls == []


def test_another_users_open_document_does_not_hold_it_back(db, model_client):
    user, someone = add_user(db), add_user(db, "other@hospital.test")
    doc = add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    jobs.enqueue(db, add_document(db, someone, "/x/theirs"))
    set_summary(db, user, "completed")
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]

    service.refresh_after_document(finished_job(doc))

    assert summary_row(db, user)[1] == "completed"
    assert summary_row(db, user)[0] != "old summary"


def test_a_summary_being_built_is_left_to_that_run(db, model_client, json_log):
    user = add_user(db)
    doc = add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    set_summary(db, user, "processing", html=None)

    service.refresh_after_document(finished_job(doc))

    assert summary_row(db, user)[1] == "processing"
    assert model_client.calls == []
    assert rerun_requested(db, user)  # that run builds again when it ends
    assert line_with(json_log, "summary_refresh_queued")["patient_id"] == user


def test_a_stale_summary_run_is_taken_over(db, model_client):
    user = add_user(db)
    doc = add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    set_summary(db, user, "processing", html=None, minutes_ago=31)
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]

    service.refresh_after_document(finished_job(doc))

    assert summary_row(db, user)[1] == "completed"


def test_the_worker_runs_its_after_job_once_per_job_and_survives_its_errors(
    db, monkeypatch
):
    seen = []

    def after(job):
        seen.append(job.document_id)
        raise RuntimeError("after-job bug")

    monkeypatch.setattr(jobs, "process", lambda job, worker: None)
    user = add_user(db)
    docs = [add_document(db, user, f"/x/{n}") for n in range(2)]
    claimed = [finished_job(d) for d in docs]
    monkeypatch.setattr(
        jobs, "claim_next", lambda conn, name: claimed.pop(0) if claimed else None
    )
    worker = jobs.Worker(poll_s=0.05, after_job=after)
    worker.start()
    try:
        for _ in range(100):
            if len(seen) == 2:
                break
            time.sleep(0.05)
    finally:
        worker.stop(timeout=5)

    assert seen == docs


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_service_publishes_no_api_docs(client, path):
    assert client.get(path).status_code == 404
