"""
Document jobs (document_jobs) against the test database, with the fake
model client: queueing, claiming with a lease, takeover and parking, every
write conditional on the claim, a job run through the graph, a crashed
worker's run resumed from its checkpoint by another, and the worker thread.
"""

import json
import time

import pytest

import document_jobs as jobs
import llm
from dataset.build import DATASET_DIR
from dataset_records import DOCS, expected_patient, key, parse_of
from document_jobs import LostClaim
from observability import context, start_run
from pipeline import checkpoints
from pipeline import graph as document_graph

PRIMARY, FALLBACK = llm.PRIMARY_MODEL, llm.FALLBACK_MODEL


# -------------------------------------------------------------------
# Rows
# -------------------------------------------------------------------
def add_document(db, doc_id: str = "d02") -> str:
    """A dataset document, uploaded by an account named as its patient (as seed-dev does)."""
    name = expected_patient(doc_id).name
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (name) VALUES ('Test Hospital')"
            " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
        )
        (org,) = cur.fetchone()
        cur.execute(
            "INSERT INTO users (org_id, email, full_name) VALUES (%s, %s, %s)"
            " RETURNING id",
            (org, f"{doc_id}-{time.monotonic_ns()}@hospital.test", name),
        )
        (user,) = cur.fetchone()
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, 'a', 'image/png', %s) RETURNING id",
            (user, str(DATASET_DIR / DOCS[doc_id]["file"])),
        )
        return str(cur.fetchone()[0])


def job_row(db, document_id) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id::text, status, attempts, claimed_by, run_id::text, error"
            " FROM jobs WHERE document_id = %s ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        )
        row = cur.fetchone()
        return dict(zip([c.name for c in cur.description], row)) if row else None


def insight_row(db, document_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, html_insights, error_message FROM insights"
            " WHERE document_id = %s",
            (document_id,),
        )
        return cur.fetchone()


def set_insight(db, document_id, status, error=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO insights (document_id, status, error_message)"
            " VALUES (%s, %s, %s) ON CONFLICT (document_id) DO UPDATE"
            " SET status = EXCLUDED.status, error_message = EXCLUDED.error_message",
            (document_id, status, error),
        )


def expire_lease(db, job_id):
    with db.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (job_id,),
        )


def replies_for(model_client, doc_id: str = "d02"):
    model_client.replies[PRIMARY] = [
        *["\n".join(p.lines) for p in key(doc_id).pages],
        json.dumps({"document_type": DOCS[doc_id]["type"]}),
        parse_of(key(doc_id)).model_dump_json(),
        "<h2>Summary</h2>",
    ]


# -------------------------------------------------------------------
# Queue
# -------------------------------------------------------------------
def test_a_new_document_is_queued_and_shows_processing(db):
    doc = add_document(db)

    assert jobs.enqueue(db, doc) == "queued"

    assert job_row(db, doc)["status"] == "queued"
    assert insight_row(db, doc) == ("processing", None, None)


def test_a_document_with_an_open_job_is_not_queued_twice(db):
    doc = add_document(db)
    jobs.enqueue(db, doc)

    assert jobs.enqueue(db, doc) == "already_queued"
    assert jobs.enqueue(db, doc, reprocess=True) == "already_queued"


@pytest.mark.parametrize("status", ["verified", "needs_review"])
def test_a_finished_document_is_left_alone_unless_reprocessed(db, status):
    doc = add_document(db)
    set_insight(db, doc, status)

    assert jobs.enqueue(db, doc) == "already_done"
    assert job_row(db, doc) is None
    assert jobs.enqueue(db, doc, reprocess=True) == "queued"
    assert insight_row(db, doc)[0] == "processing"


def test_a_failed_document_is_queued_again_with_its_error_cleared(db):
    doc = add_document(db)
    set_insight(db, doc, "failed", error="earlier error")

    assert jobs.enqueue(db, doc) == "queued"
    assert insight_row(db, doc) == ("processing", None, None)


@pytest.mark.parametrize("doc", ["00000000-0000-0000-0000-000000000000", "not-a-uuid"])
def test_an_unknown_document_is_not_queued(db, doc):
    assert jobs.enqueue(db, doc) == "no_such_document"


# -------------------------------------------------------------------
# Claims
# -------------------------------------------------------------------
def test_the_oldest_job_is_claimed_for_a_lease(db):
    first, second = add_document(db), add_document(db)
    jobs.enqueue(db, first)
    jobs.enqueue(db, second)

    job = jobs.claim_next(db, "A")

    assert (job.document_id, job.attempts, job.run_id) == (first, 1, None)
    row = job_row(db, first)
    assert (row["status"], row["claimed_by"], row["attempts"]) == ("running", "A", 1)
    assert jobs.claim_next(db, "B").document_id == second
    assert jobs.claim_next(db, "C") is None


def test_a_job_whose_lease_ran_out_is_taken_over(db):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    assert jobs.claim_next(db, "B") is None  # A's lease still holds

    expire_lease(db, job.id)
    taken = jobs.claim_next(db, "B")

    assert (taken.id, taken.attempts) == (job.id, 2)
    assert job_row(db, doc)["claimed_by"] == "B"


def test_a_job_claimed_too_often_is_parked(db, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 2)
    doc = add_document(db)
    jobs.enqueue(db, doc)
    for worker in ("A", "B"):
        job = jobs.claim_next(db, worker)
        expire_lease(db, job.id)

    assert jobs.claim_next(db, "C") is None

    row = job_row(db, doc)
    assert (row["status"], row["claimed_by"], row["attempts"]) == ("parked", None, 3)
    message = jobs.PARKED.format(n=2)
    assert row["error"] == message
    assert insight_row(db, doc) == ("failed", None, message)
    assert jobs.enqueue(db, doc) == "queued"  # it can be requested again


def test_a_parked_job_s_run_is_ended(db, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 1)
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    run = start_run(doc, "test")
    run.close()  # the worker died with its run open
    jobs.set_run(db, job, "A", run.id)
    expire_lease(db, job.id)

    jobs.claim_next(db, "B")

    with db.cursor() as cur:
        cur.execute(
            "SELECT end_state, error FROM pipeline_runs WHERE id = %s", (run.id,)
        )
        assert cur.fetchone() == ("failed", jobs.PARKED.format(n=1))


@pytest.mark.parametrize(
    "write",
    [
        lambda db, job, w: jobs.renew_lease(db, job, w),
        lambda db, job, w: jobs.set_run(db, job, w, None),
        lambda db, job, w: jobs.complete(db, job, w, {"end_state": "verified"}),
        lambda db, job, w: jobs.fail(db, job, w, "late error"),
    ],
    ids=["renew", "set-run", "complete", "fail"],
)
def test_a_worker_that_lost_its_claim_writes_nothing(db, write):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    expire_lease(db, job.id)
    jobs.claim_next(db, "B")

    with pytest.raises(LostClaim):
        write(db, job, "A")

    row = job_row(db, doc)
    assert (row["status"], row["claimed_by"], row["error"]) == ("running", "B", None)
    assert insight_row(db, doc) == ("processing", None, None)


def test_the_heartbeat_renews_the_lease_and_notices_a_lost_claim(db):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    expire_lease(db, job.id)

    with jobs.Heartbeat(job, "A", every=0.05) as beat:
        time.sleep(0.3)
        assert jobs.claim_next(db, "B") is None  # renewed: still A's
        assert not beat.lost.is_set()
        expire_lease(db, job.id)
        while jobs.claim_next(db, "B") is None:  # a renewal may land first
            expire_lease(db, job.id)
        for _ in range(50):
            if beat.lost.is_set():
                break
            time.sleep(0.05)

    assert beat.lost.is_set()


# -------------------------------------------------------------------
# Running a job
# -------------------------------------------------------------------
def test_a_job_runs_the_document_through_the_graph(db, model_client):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    replies_for(model_client)

    jobs.process(job, "A")

    status, html, error = insight_row(db, doc)
    assert (status, error) == ("verified", None)
    assert "<h2>Summary</h2>" in html
    row = job_row(db, doc)
    assert (row["status"], row["claimed_by"]) == ("done", None)
    with db.cursor() as cur:
        cur.execute(
            "SELECT end_state FROM pipeline_runs WHERE id = %s", (row["run_id"],)
        )
        assert cur.fetchone() == ("verified",)
        cur.execute("SELECT extracted_markdown FROM documents WHERE id = %s", (doc,))
        assert cur.fetchone()[0] == "\n".join(key("d02").pages[0].lines)
        cur.execute("SELECT run_id::text FROM document_records WHERE document_id = %s",
                    (doc,))  # fmt: skip
        assert cur.fetchone() == (row["run_id"],)


def test_a_job_that_fails_stores_the_error_on_the_job_and_the_document(
    db, model_client
):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    model_client.replies[PRIMARY] = [ConnectionError("down")]
    model_client.replies[FALLBACK] = [ConnectionError("down")]

    jobs.process(job, "A")

    row = job_row(db, doc)
    assert row["status"] == "failed" and "No model produced" in row["error"]
    status, _, error = insight_row(db, doc)
    assert status == "failed" and error == row["error"]
    with db.cursor() as cur:
        cur.execute(
            "SELECT end_state FROM pipeline_runs WHERE id = %s", (row["run_id"],)
        )
        assert cur.fetchone() == ("failed",)


def test_a_deleted_document_s_job_is_gone_with_it(db, model_client):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    with db.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id = %s", (doc,))

    jobs.process(job, "A")  # nothing to write to; nothing raised

    assert job_row(db, doc) is None
    assert model_client.calls == []


def test_a_crashed_worker_s_run_is_resumed_by_the_next_and_its_late_write_refused(
    db, model_client
):
    doc = add_document(db)
    jobs.enqueue(db, doc)
    job = jobs.claim_next(db, "A")
    replies_for(model_client)

    # Worker A transcribes the page, then dies before classifying: its run
    # is left open, with the graph's state checkpointed under the run's id.
    run = start_run(doc, document_graph.GRAPH_VERSION)
    jobs.set_run(db, job, "A", run.id)
    with checkpoints.open_checkpointer() as saver:
        graph = document_graph.build(checkpointer=saver, interrupt_before=["classify"])
        token = context.current_run.set(run)
        try:
            graph.invoke(
                jobs._document_state(db, doc),
                {"configurable": {"thread_id": run.id}, "recursion_limit": 100},
            )
        finally:
            context.current_run.reset(token)
            run.close()
    expire_lease(db, job.id)

    taken = jobs.claim_next(db, "B")
    assert (taken.run_id, taken.attempts) == (run.id, 2)
    jobs.process(taken, "B")

    assert insight_row(db, doc)[0] == "verified"
    with db.cursor() as cur:
        cur.execute(
            "SELECT node FROM pipeline_steps WHERE run_id = %s ORDER BY seq", (run.id,)
        )
        assert [r[0] for r in cur.fetchall()] == [
            "open", "transcribe", "classify", "parse", "validate", "render", "finish",
        ]  # fmt: skip
        cur.execute("SELECT end_state FROM pipeline_runs WHERE id = %s", (run.id,))
        assert cur.fetchone() == ("verified",)
    # The page was transcribed once: B went on from A's checkpoint.
    vision_calls = [c for c in model_client.calls if c["messages"][-1].get("images")]
    assert len(vision_calls) == 1
    with pytest.raises(LostClaim):
        jobs.fail(db, job, "A", "A's late error")
    assert insight_row(db, doc)[0] == "verified"


# -------------------------------------------------------------------
# The worker thread
# -------------------------------------------------------------------
def test_the_worker_takes_queued_jobs_until_stopped(db, model_client):
    doc = add_document(db)
    replies_for(model_client)
    worker = jobs.Worker(poll_s=0.05)
    worker.start()
    try:
        jobs.enqueue(db, doc)
        worker.wake.set()
        for _ in range(200):
            if insight_row(db, doc)[0] != "processing":
                break
            time.sleep(0.05)
    finally:
        worker.stop(timeout=10)

    assert insight_row(db, doc)[0] == "verified"
    assert job_row(db, doc)["status"] == "done"
