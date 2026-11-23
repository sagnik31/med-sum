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
  LostClaim and writes nothing more. So is every write of its run (a step
  started, the record saved, the run ended): the run carries a JobClaim
  checked in each of those transactions, and a worker whose lease ran out
  without a renewal (the database unreachable) counts its claim as lost.
  A worker that lost its claim leaves the run and its checkpoints alone:
  they now belong to the worker that took the job over.
- A run that ends checks_passed is picked for a clinician's audit at
  MEDSUM_AUDIT_SAMPLE_RATE (default 0.10), in the same transaction as its
  result: see audit_sampled. No run ends verified: every checks_passed
  document waits for a clinician's sign-off, and an audit sample is one
  whose sign-off checks every value
  (docs/adr/0013-model-choice-after-the-first-live-evals.md).

Log lines carry ids, counts and statuses only; error messages go to the
database (jobs.error, insights.error_message), never the log.
"""

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass

import psycopg

from observability import ClaimLost, resume_run, start_run
from observability.redact import redact
from pipeline import checkpoints, nodes
from pipeline import graph as document_graph
from pipeline.nodes import plain_text
from pipeline.state import initial_state
from validators import ExpectedPatient

logger = logging.getLogger("medsum.jobs")

LEASE = os.environ.get("MEDSUM_JOB_LEASE", "5 minutes")
HEARTBEAT_S = float(os.environ.get("MEDSUM_JOB_HEARTBEAT_S", "60"))
MAX_ATTEMPTS = int(os.environ.get("MEDSUM_JOB_MAX_ATTEMPTS", "3"))
POLL_S = 5.0


def _sample_rate(raw: str) -> float:
    rate = float(raw)
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"MEDSUM_AUDIT_SAMPLE_RATE must be between 0 and 1, not {raw}")
    return rate


# The share of checks_passed documents that go back to a clinician as an
# audit: every value checked before the sign-off.
AUDIT_SAMPLE_RATE = _sample_rate(os.environ.get("MEDSUM_AUDIT_SAMPLE_RATE", "0.10"))

# Insight statuses a document stays in until it is reprocessed: the
# run's end states, and verified (a clinician signed it off).
END_STATES = ("checks_passed", "verified", "needs_review")
PARKED = "stopped after {n} attempts: the worker died each time"


class LostClaim(ClaimLost):
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
                "INSERT INTO insights (document_id, status)"
                " SELECT id, 'processing' FROM documents WHERE id = %s"
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
        "INSERT INTO insights (document_id, status, html_insights,"
        " error_message)"
        " SELECT id, %(s)s, %(h)s, %(e)s FROM documents WHERE id = %(d)s"
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


def renew_lease(conn, job: Job, worker: str) -> float:
    """Renew the job's lease; the seconds it now has left."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET lease_until = now() + %(lease)s::interval,"
            " updated_at = now()"
            " WHERE id = %(job)s AND claimed_by = %(worker)s AND status = 'running'"
            " RETURNING extract(epoch FROM lease_until - now())",
            {"lease": LEASE, "job": job.id, "worker": worker},
        )
        row = cur.fetchone()
    if row is None:
        raise LostClaim(f"job {job.id} is no longer claimed by {worker}")
    return float(row[0])


def set_run(conn, job: Job, worker: str, run_id: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        _held(cur, job, worker, "UPDATE jobs SET run_id = %(run)s, updated_at = now()",
              {"run": run_id})  # fmt: skip


def audit_sampled(run_id: str, rate: float | None = None) -> bool:
    """
    Whether a checks_passed run goes back to a clinician as an audit. The
    pick comes from a hash of the run's id, not a random draw, so a run
    resumed by another worker is picked the same way; over many runs it
    picks the given share (default AUDIT_SAMPLE_RATE). Nothing in such a
    document raised a signal, so only a sample checked value by value shows
    how many wrong values get through without one. See
    docs/adr/0011-clinician-review.md.
    """
    rate = AUDIT_SAMPLE_RATE if rate is None else rate
    digest = hashlib.sha256(f"audit-sample:{run_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


def complete(
    conn, job: Job, worker: str, final: dict, *, run_id: str | None = None
) -> bool:
    """
    Store the run's result, in one transaction with ending the job: the
    end state and insight page on insights, the transcription as plain
    text in documents.extracted_markdown (a copy for reading; a reprocess
    reuses the transcribe steps of earlier runs, not this column), and,
    for a checks_passed run picked by audit_sampled, its audit sample. Returns
    whether it was picked.
    """
    sampled = (
        final["end_state"] == "checks_passed"
        and run_id is not None
        and audit_sampled(run_id)
    )
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
        if sampled:
            cur.execute(
                "INSERT INTO audit_samples (run_id, document_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (run_id, job.document_id),
            )
    return sampled


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
    """
    Renews a job's lease every HEARTBEAT_S seconds until stopped. `lost` is
    set when the claim is gone, or when the lease has run out without a
    renewal (the database couldn't be reached): another worker may have
    taken the job over by then, so this one must not go on writing.
    """

    def __init__(self, job: Job, worker: str, *, every: float = HEARTBEAT_S):
        self.job, self.worker, self.every = job, worker, every
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._deadline = 0.0  # time.monotonic() when the lease runs out

    def __enter__(self):
        # Renewed once first, so the lease's end is known from the start.
        with connect() as conn:
            self._renewed(renew_lease(conn, self.job, self.worker))
        self._thread.start()
        return self

    def _renewed(self, seconds_left: float) -> None:
        self._deadline = time.monotonic() + seconds_left

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        return False

    def _beat(self):
        while not self._stop.wait(self.every):
            try:
                with connect() as conn:
                    self._renewed(renew_lease(conn, self.job, self.worker))
            except LostClaim:
                self.lost.set()
                return
            except Exception:
                logger.exception(
                    "could not renew the job's lease",
                    extra=_obs("job_lease_not_renewed", job_id=self.job.id),
                )
                if time.monotonic() >= self._deadline:
                    self.lost.set()
                    return


class JobClaim:
    """
    A job's claim as its run checks it (observability.Run.claim): check(cur)
    raises LostClaim unless this worker still holds the job, with a lease
    that hasn't run out, locking the job's row for the rest of the
    transaction so no other worker can take it over until the write is
    done. See docs/adr/0006-durable-traceable-runs.md.
    """

    def __init__(self, job: Job, worker: str, beat: Heartbeat):
        self.job, self.worker, self.beat = job, worker, beat

    def check(self, cur) -> None:
        if not self.beat.lost.is_set():
            cur.execute(
                "SELECT 1 FROM jobs WHERE id = %(job)s AND claimed_by = %(worker)s"
                " AND status = 'running' AND lease_until > now() FOR UPDATE",
                {"job": self.job.id, "worker": self.worker},
            )
            if cur.fetchone() is not None:
                return
            self.beat.lost.set()
        raise LostClaim(f"job {self.job.id} is no longer claimed by {self.worker}")


def _document_state(conn, document_id: str) -> dict:
    """
    The graph's first state for a document: its file, who uploaded it, and
    the patient record it was uploaded to, which the patient-match check
    compares the printed details with (name, sex, date of birth or age, and
    the MRN). A deleted document is not processed.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.storage_path, u.full_name, p.full_name, p.sex,"
            " p.date_of_birth, p.age_years, p.mrn"
            " FROM documents d JOIN patients p ON p.id = d.patient_id"
            " LEFT JOIN staff_users u ON u.id = d.uploaded_by"
            " WHERE d.id = %s AND d.deleted_at IS NULL",
            (document_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError("the document was deleted")
    path, uploader, name, sex, born, age, mrn = row
    return initial_state(
        document_id,
        file_path=path,
        uploaded_by=uploader,
        expected_patient=ExpectedPatient(
            name=name, sex=sex, date_of_birth=born, age_years=age, mrn=mrn
        ),
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
    # A resumed run starts with no identifiers noted: note what its
    # checkpoint holds, so what it stores from now on is masked.
    # See docs/adr/0008-patient-data-protection.md.
    run.add_identifiers(*nodes.state_identifiers(saved.values or state))
    if saved.values and not saved.next:
        return saved.values  # it finished before the worker died
    return graph.invoke(None if saved.values else state, config)


def _delete_thread(saver, run_id: str) -> None:
    """
    The run's checkpoints, deleted once the graph has stopped: the run has
    ended, so it won't resume, and they hold document content. The database
    deletes them too when the run ends or goes with its document; this
    catches a checkpoint the graph wrote after its document was deleted.
    See docs/adr/0008-patient-data-protection.md.
    """
    try:
        saver.delete_thread(run_id)
    except Exception:
        logger.warning(
            "could not delete the run's checkpoints",
            extra=_obs("checkpoints_not_deleted", run_id=run_id),
        )


def process(job: Job, worker: str, *, build=None, heartbeat_s: float = HEARTBEAT_S):
    """
    Run one claimed job to its end. Errors are stored, not raised. `build`
    makes the graph from a checkpointer (default: pipeline.graph.build).
    """
    known: list[str] = []  # the patient's identifiers, once known
    with connect() as conn:
        try:
            state = _document_state(conn, job.document_id)
            known = nodes.expected_identifiers(state)
            with (
                checkpoints.open_checkpointer() as saver,
                Heartbeat(job, worker, every=heartbeat_s) as beat,
            ):
                compiled = (build or document_graph.build)(checkpointer=saver)
                run_id = None
                try:
                    run = _open_run(conn, job, worker)
                    run.add_identifiers(*nodes.expected_identifiers(state))
                    # A failure from here on is masked with every identifier
                    # the run has noted, the printed patient's included.
                    known = run.identifiers
                    run.claim = JobClaim(job, worker, beat)
                    with run:
                        run_id = run.id
                        final = _invoke(compiled, run, state)
                        run.finish(final["end_state"])  # checks the claim
                finally:
                    # The checkpoints of a run this worker lost are the new
                    # owner's, who resumes from them.
                    if run_id is not None and not beat.lost.is_set():
                        _delete_thread(saver, run_id)
            sampled = complete(conn, job, worker, final, run_id=run_id)
            logger.info(
                "document job completed",
                extra=_obs("job_completed", document_id=job.document_id,
                           job_id=job.id, end_state=final["end_state"],
                           audit_sampled=sampled),
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
                # Shown to the user and stored on the job: identifiers masked.
                message = redact(str(e) or type(e).__name__, known)
                fail(conn, job, worker, message)
            except LostClaim:
                pass


# -------------------------------------------------------------------
# The worker
# -------------------------------------------------------------------
class Worker:
    """
    One thread taking jobs in turn: one Ollama job at a time per process.
    `after_job(job)`, if given, runs in the same thread after each job
    (whatever its outcome), so work it starts doesn't compete with the next
    document for the model; an error in it is logged, never raised.
    """

    def __init__(self, *, poll_s: float = POLL_S, after_job=None):
        self.name = worker_id()
        self.poll_s = poll_s
        self.after_job = after_job
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

    def _after(self, job: Job) -> None:
        if self.after_job is None:
            return
        try:
            self.after_job(job)
        except Exception:
            logger.exception("the work after a job failed",
                             extra=_obs("after_job_error", job_id=job.id,
                                        document_id=job.document_id))  # fmt: skip

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with connect() as conn:
                    job = claim_next(conn, self.name)
                if job is not None:
                    process(job, self.name)
                    self._after(job)
                    continue
            except Exception:
                logger.exception("the job worker hit an error",
                                 extra=_obs("worker_error"))  # fmt: skip
            self.wake.wait(self.poll_s)
            self.wake.clear()
