"""
Document jobs: a queue in Postgres (the jobs table), worked by one worker
thread per AI service process, one document at a time.

- enqueue() adds a job for a document unless one is open, or (unless it is
  a reprocess) the document already reached an end state. The document's
  insights row shows processing from then on.
- claim_next() takes the oldest open job whose lease is free: queued, or
  running under a lease that ran out (its worker died). FOR UPDATE SKIP
  LOCKED keeps two workers off the same job. A job claimed more than
  MAX_ATTEMPTS times is parked instead and its document marked failed,
  so a document that kills its worker can't loop.
- process() runs the claimed job's document through the graph inside a
  pipeline run, with the graph's state checkpointed under the run's id. A
  job taken over from a dead worker resumes that run from its last
  checkpoint. While it runs, a heartbeat thread renews the lease.
- Every write for a job (lease, run id, result, failure) is conditional on
  the worker still holding the claim; a worker that lost it gets
  LostClaim and writes nothing more.

Log lines carry ids, counts and statuses only; error messages go to the
database (jobs.error, insights.error_message), never the log.
"""

import logging
import os
import threading
import uuid
from dataclasses import dataclass

import psycopg

from observability import resume_run, start_run
from pipeline import checkpoints
from pipeline import graph as document_graph
from pipeline.nodes import plain_text
from pipeline.state import initial_state
from validators import ExpectedPatient

logger = logging.getLogger("medsum.jobs")

LEASE = os.environ.get("MEDSUM_JOB_LEASE", "5 minutes")
HEARTBEAT_S = float(os.environ.get("MEDSUM_JOB_HEARTBEAT_S", "60"))
MAX_ATTEMPTS = int(os.environ.get("MEDSUM_JOB_MAX_ATTEMPTS", "3"))
POLL_S = 5.0

# Insight statuses a document stays in until it is reprocessed.
END_STATES = ("verified", "needs_review")
PARKED = "stopped after {n} attempts: the worker died each time"


class LostClaim(RuntimeError):
    """This worker no longer holds the job's claim."""


@dataclass(frozen=True)
class Job:
    id: str
    document_id: str
    attempts: int
    run_id: str | None


def _obs(event: str, **fields) -> dict:
    return {"obs": {"event": event, **fields}}


def connect() -> psycopg.Connection:
    """DATABASE_URL, as the Go backend; autocommit, with explicit transactions."""
    return psycopg.connect(checkpoints.database_url(), autocommit=True)


def worker_id() -> str:
    """This worker's name in jobs.claimed_by: host, process and a random part."""
    return (
        f"{os.environ.get('COMPUTERNAME', 'host')}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    )


# -------------------------------------------------------------------
# Queue
# -------------------------------------------------------------------
def enqueue(conn, document_id: str, *, reprocess: bool = False) -> str:
    """
    Queue a job for the document. Returns what happened: "queued",
    "already_queued", "already_done" (an end state and not a reprocess),
    or "no_such_document".
    """
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT d.id, i.status FROM documents d"
                " LEFT JOIN insights i ON i.document_id = d.id"
                " WHERE d.id = %s FOR UPDATE OF d",
                (document_id,),
            )
            row = cur.fetchone()
            if row is None:
                return "no_such_document"
            if row[1] in END_STATES and not reprocess:
                return "already_done"
            cur.execute(
                "INSERT INTO jobs (document_id) VALUES (%s)"
                " ON CONFLICT (document_id) WHERE status IN ('queued', 'running')"
                " DO NOTHING RETURNING id",
                (document_id,),
            )
            job = cur.fetchone()
            if job is None:
                return "already_queued"
            cur.execute(
                "INSERT INTO insights (document_id, user_id, status)"
                " SELECT id, user_id, 'processing' FROM documents WHERE id = %s"
                " ON CONFLICT (document_id) DO UPDATE SET status = 'processing',"
                " error_message = NULL, updated_at = now()",
                (document_id,),
            )
    except psycopg.errors.InvalidTextRepresentation:
        return "no_such_document"  # not a uuid
    logger.info(
        "document job queued",
        extra=_obs("job_queued", document_id=str(document_id), job_id=str(job[0]),
                   reprocess=reprocess),
    )  # fmt: skip
    return "queued"


def claim_next(conn, worker: str) -> Job | None:
    """The next job for this worker, or None when there is none."""
    while True:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'running', claimed_by = %(w)s,"
                " lease_until = now() + %(lease)s::interval,"
                " attempts = attempts + 1, updated_at = now()"
                " WHERE id = (SELECT id FROM jobs"
                "   WHERE status = 'queued'"
                "      OR (status = 'running' AND lease_until < now())"
                "   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)"
                " RETURNING id, document_id, attempts, run_id",
                {"w": worker, "lease": LEASE},
            )
            row = cur.fetchone()
        if row is None:
            return None
        job = Job(str(row[0]), str(row[1]), row[2], row[3] and str(row[3]))
        if job.attempts <= MAX_ATTEMPTS:
            logger.info(
                "document job claimed",
                extra=_obs("job_claimed", document_id=job.document_id,
                           job_id=job.id, attempt=job.attempts),
            )  # fmt: skip
            return job
        _park(conn, job, worker)


def _park(conn, job: Job, worker: str) -> None:
    message = PARKED.format(n=MAX_ATTEMPTS)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = 'parked', claimed_by = NULL, lease_until = NULL,"
            " error = %s, updated_at = now()"
            " WHERE id = %s AND claimed_by = %s AND status = 'running'",
            (message, job.id, worker),
        )
        if cur.rowcount:
            _set_insight(cur, job.document_id, "failed", error=message)
    if job.run_id is not None:
        try:
            with resume_run(job.run_id) as run:
                run.finish("failed", error=message)
        except Exception:
            pass  # already ended, or gone with its document
    logger.error(
        "document job parked",
        extra=_obs("job_parked", document_id=job.document_id, job_id=job.id,
                   attempt=job.attempts),
    )  # fmt: skip


def _set_insight(cur, document_id, status, *, html=None, error=None) -> None:
    cur.execute(
        "INSERT INTO insights (document_id, user_id, status, html_insights,"
        " error_message)"
        " SELECT id, user_id, %(s)s, %(h)s, %(e)s FROM documents WHERE id = %(d)s"
        " ON CONFLICT (document_id) DO UPDATE SET status = EXCLUDED.status,"
        " html_insights = coalesce(EXCLUDED.html_insights, insights.html_insights),"
        " error_message = EXCLUDED.error_message, updated_at = now()",
        {"s": status, "h": html, "e": error, "d": document_id},
    )


def _held(cur, job: Job, worker: str, sql: str, params: dict) -> None:
    """Run an UPDATE of the job that applies only while the claim is held."""
    cur.execute(
        sql + " WHERE id = %(job)s AND claimed_by = %(worker)s AND status = 'running'",
        {**params, "job": job.id, "worker": worker},
    )
    if cur.rowcount == 0:
        raise LostClaim(f"job {job.id} is no longer claimed by {worker}")


def renew_lease(conn, job: Job, worker: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        _held(cur, job, worker,
              "UPDATE jobs SET lease_until = now() + %(lease)s::interval,"
              " updated_at = now()", {"lease": LEASE})  # fmt: skip


def set_run(conn, job: Job, worker: str, run_id: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        _held(cur, job, worker, "UPDATE jobs SET run_id = %(run)s, updated_at = now()",
              {"run": run_id})  # fmt: skip


def complete(conn, job: Job, worker: str, final: dict) -> None:
    """
    Store the run's result, in one transaction with ending the job: the
    end state and insight page on insights, and the transcription in
    documents.extracted_markdown (which the patient summary reads).
    """
    with conn.transaction(), conn.cursor() as cur:
        _held(cur, job, worker,
              "UPDATE jobs SET status = 'done', claimed_by = NULL,"
              " lease_until = NULL, error = NULL, updated_at = now()", {})  # fmt: skip
        _set_insight(cur, job.document_id, final["end_state"],
                     html=final.get("insight_html"))  # fmt: skip
        cur.execute(
            "UPDATE documents SET extracted_markdown = %s, updated_at = now()"
            " WHERE id = %s",
            (plain_text(final.get("transcriptions", [])), job.document_id),
        )


def fail(conn, job: Job, worker: str, message: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        _held(cur, job, worker,
              "UPDATE jobs SET status = 'failed', claimed_by = NULL,"
              " lease_until = NULL, error = %(e)s, updated_at = now()",
              {"e": message})  # fmt: skip
        _set_insight(cur, job.document_id, "failed", error=message)


# -------------------------------------------------------------------
# Running a job
# -------------------------------------------------------------------
class Heartbeat:
    """Renews a job's lease every HEARTBEAT_S seconds until stopped."""

    def __init__(self, job: Job, worker: str, *, every: float = HEARTBEAT_S):
        self.job, self.worker, self.every = job, worker, every
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        return False

    def _beat(self):
        while not self._stop.wait(self.every):
            try:
                with connect() as conn:
                    renew_lease(conn, self.job, self.worker)
            except LostClaim:
                self.lost.set()
                return
            except Exception:
                logger.exception(
                    "could not renew the job's lease",
                    extra=_obs("job_lease_not_renewed", job_id=self.job.id),
                )


def _document_state(conn, document_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.storage_path, u.full_name FROM documents d"
            " LEFT JOIN users u ON u.id = d.user_id WHERE d.id = %s",
            (document_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError("the document was deleted")
    path, uploader = row
    # Until patients are records of their own, the uploader's name is all
    # that is known of who the document is for.
    return initial_state(
        document_id,
        file_path=path,
        uploaded_by=uploader,
        expected_patient=ExpectedPatient(name=uploader) if uploader else None,
    )


def _open_run(conn, job: Job, worker: str):
    """The job's run: the one it left off (after a takeover), or a new one."""
    if job.run_id is not None:
        try:
            run = resume_run(job.run_id)
            logger.info(
                "document job resumed its run",
                extra=_obs("job_resumed", job_id=job.id, run_id=job.run_id),
            )
            return run
        except Exception:
            pass  # the run ended or is gone: start again
    run = start_run(job.document_id, document_graph.GRAPH_VERSION)
    set_run(conn, job, worker, run.id)
    return run


def _invoke(graph, run, state: dict) -> dict:
    """Run the graph for the run's thread, from its checkpoint if it has one."""
    config = {
        "recursion_limit": document_graph.RECURSION_LIMIT,
        "configurable": {"thread_id": run.id},
    }
    saved = graph.get_state(config)
    if saved.values and not saved.next:
        return saved.values  # it finished before the worker died
    return graph.invoke(None if saved.values else state, config)


def process(job: Job, worker: str, *, build=None, heartbeat_s: float = HEARTBEAT_S):
    """
    Run one claimed job to its end. Errors are stored, not raised. `build`
    makes the graph from a checkpointer (default: pipeline.graph.build).
    """
    with connect() as conn:
        try:
            state = _document_state(conn, job.document_id)
            with (
                checkpoints.open_checkpointer() as saver,
                Heartbeat(job, worker, every=heartbeat_s) as beat,
            ):
                compiled = (build or document_graph.build)(checkpointer=saver)
                with _open_run(conn, job, worker) as run:
                    final = _invoke(compiled, run, state)
                    if beat.lost.is_set():
                        raise LostClaim(f"job {job.id} lost its claim while running")
                    renew_lease(conn, job, worker)  # still ours, before ending
                    run.finish(final["end_state"])
            complete(conn, job, worker, final)
            logger.info(
                "document job completed",
                extra=_obs("job_completed", document_id=job.document_id,
                           job_id=job.id, end_state=final["end_state"]),
            )  # fmt: skip
        except LostClaim:
            logger.warning(
                "document job lost its claim; its result is not saved",
                extra=_obs(
                    "job_claim_lost", document_id=job.document_id, job_id=job.id
                ),
            )
        except Exception as e:
            logger.exception(
                "document job failed",
                extra=_obs("job_failed", document_id=job.document_id, job_id=job.id),
            )
            try:
                fail(conn, job, worker, str(e) or type(e).__name__)
            except LostClaim:
                pass


# -------------------------------------------------------------------
# The worker
# -------------------------------------------------------------------
class Worker:
    """One thread taking jobs in turn: one Ollama job at a time per process."""

    def __init__(self, *, poll_s: float = POLL_S):
        self.name = worker_id()
        self.poll_s = poll_s
        self.wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="document-jobs",
                                        daemon=True)  # fmt: skip

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        self.wake.set()
        self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with connect() as conn:
                    job = claim_next(conn, self.name)
                if job is not None:
                    process(job, self.name)
                    continue
            except Exception:
                logger.exception("the job worker hit an error",
                                 extra=_obs("worker_error"))  # fmt: skip
            self.wake.wait(self.poll_s)
            self.wake.clear()
