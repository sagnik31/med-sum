#!/usr/bin/env python

"""
Insights Generation Service (FastAPI)

- Exposes:
    GET  /health
    POST /internal/generate-insights        (queue a document's job)
    POST /internal/reprocess-document       (queue it again, as a new run)
    POST /internal/generate-user-insights   (background, per user)

- Patient summaries: built on request, and rebuilt by the worker after a
  document job ends when its uploader already has one and has no other
  document queued or running (refresh_after_document), so a batch of
  uploads rebuilds it once, at the end.

- Internal only: /internal/* requires the X-Internal-Token header to match
  INTERNAL_API_TOKEN. No CORS, so browsers cannot call it; the Go backend
  checks access to the document and forwards requests here.

- Documents: a request queues a job (document_jobs, the jobs table), and a
  worker thread started with the app takes jobs one at a time and runs each
  document through the pipeline graph (pipeline.graph): transcribe,
  classify, parse with repair and the fallback model, validate, render.
  The document ends verified or needs_review, or failed with its error.
  MEDSUM_WORKER=0 starts the app without the worker (tests).

- Logs: structured JSON lines on stderr and in config.LOG_FILE, set up when
  the app starts (observability.configure_logging). They carry ids, events
  and sizes, never document content or exception messages.
"""

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
import psycopg

import os

import config
import document_jobs
import model_health
import settings
from document_jobs import LostClaim
from observability import configure_logging
from pipeline import checkpoints


# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
logger = logging.getLogger("medsum.service")


def _obs(event: str, **fields) -> dict:
    """`extra` for a log line: an event name and ids or counts, never content."""
    return {"obs": {"event": event, **fields}}


# -------------------------------------------------------------------
# FastAPI app + internal auth
# -------------------------------------------------------------------
# Shared with the Go backend (same variable name there).
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")


def worker_enabled() -> bool:
    return os.environ.get("MEDSUM_WORKER", "1") != "0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fails here, at start-up, when DATABASE_URL is missing outside
    # MEDSUM_ENV=dev, instead of at the first job.
    settings.load()
    configure_logging(log_file=config.LOG_FILE)
    logger.info("service started", extra=_obs("service_started"))
    if not INTERNAL_API_TOKEN:
        logger.warning(
            "INTERNAL_API_TOKEN is not set; /internal/* will reject all calls",
            extra=_obs("internal_token_missing"),
        )
    worker = None
    if worker_enabled():
        checkpoints.setup()  # the checkpoint tables, created or migrated
        worker = document_jobs.Worker(after_job=refresh_after_document)
        worker.start()
    app.state.worker = worker
    try:
        yield
    finally:
        if worker is not None:
            worker.stop(timeout=5)


app = FastAPI(title="Insights Generation Service", lifespan=lifespan)


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    """Endpoints act on any document/user ID, so only the backend may call them."""
    if not INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=503, detail="INTERNAL_API_TOKEN is not configured"
        )
    if not hmac.compare_digest(x_internal_token.encode(), INTERNAL_API_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")


# A patient summary job 'processing' this long is treated as abandoned and
# may be claimed again.
STALE_PROCESSING_AFTER = "30 minutes"


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------
class GenerateInsightsRequest(BaseModel):
    document_id: str


class GenerateUserInsightsRequest(BaseModel):
    user_id: str


# -------------------------------------------------------------------
# Real worker — calls your VLM + SLM pipeline
# -------------------------------------------------------------------


def get_db_connection():
    """Postgres at DATABASE_URL (settings.database_url)."""
    return psycopg.connect(settings.database_url())


def request_document(document_id: str, *, reprocess: bool = False) -> str:
    """Queue the document's job and wake the worker; see document_jobs.enqueue."""
    with document_jobs.connect() as conn:
        result = document_jobs.enqueue(conn, document_id, reprocess=reprocess)
    worker = getattr(app.state, "worker", None)
    if worker is not None:
        worker.wake.set()
    return result


# -------------------------------------------------------------------


@app.get("/health")
async def health():
    """
    The service is up, and each model's circuit breaker (model_health):
    closed, open (skipped until its cool-down ends) or half-open (being
    probed). A model appears once it has been called.
    """
    return {"status": "ok", "model_health": model_health.snapshot()}


@app.post("/internal/generate-insights", dependencies=[Depends(require_internal_token)])
def generate_insights_endpoint(req: GenerateInsightsRequest):
    """
    Queue processing for a document. Called by the Go backend after it has
    checked access to the document. A document that already reached an
    end state, or has a job open, is left as it is; `result` says which.
    Plain `def`: the database call blocks, so FastAPI runs it in a thread.
    """
    logger.info(
        "document job requested",
        extra=_obs("job_requested", document_id=req.document_id),
    )
    result = request_document(req.document_id)
    return {"status": "accepted", "document_id": req.document_id, "result": result}


@app.post(
    "/internal/reprocess-document", dependencies=[Depends(require_internal_token)]
)
def reprocess_document_endpoint(req: GenerateInsightsRequest):
    """
    Queue a document again even if it reached an end state, e.g. after the
    pipeline changed: a new run, whose record replaces the last one. A
    document with a job open is left to that job.
    """
    logger.info(
        "document reprocess requested",
        extra=_obs("job_requested", document_id=req.document_id, reprocess=True),
    )
    result = request_document(req.document_id, reprocess=True)
    return {"status": "accepted", "document_id": req.document_id, "result": result}


@app.post(
    "/internal/generate-user-insights", dependencies=[Depends(require_internal_token)]
)
def generate_user_insights_endpoint(
    req: GenerateUserInsightsRequest,
    background_tasks: BackgroundTasks,
):
    """
    Queue the cumulative summary across all of a user's processed documents.

    Called by the Go backend (POST /user/insights) for the logged-in user.
    Plain `def` (not `async def`) on purpose: the DB call blocks, so FastAPI
    must run this in its threadpool or it freezes the event loop.
    """
    logger.info(
        "patient summary requested",
        extra=_obs("summary_requested", user_id=req.user_id),
    )

    claim = claim_patient_summary(req.user_id)
    if claim is not None:
        background_tasks.add_task(run_patient_summary, req.user_id, claim)
    return {"status": "accepted" if claim is not None else "already_processing"}


def claim_patient_summary(user_id: str) -> datetime | None:
    """
    Mark the user's summary processing, unless a live run has it: one that
    has gone stale (STALE_PROCESSING_AFTER) is taken over.

    Returns the claim (the patient_insights_updated_at it set), or None when
    a live run holds it. Nothing else writes that column while a run is
    processing, so the timestamp identifies this claim: a takeover sets a
    new one, at least STALE_PROCESSING_AFTER later.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET patient_insights_status = 'processing',
                    patient_insights_error = NULL,
                    patient_insights_updated_at = NOW()
                WHERE id = %s
                  AND (patient_insights_status IS DISTINCT FROM 'processing'
                       OR patient_insights_updated_at < NOW() - %s::interval)
                RETURNING patient_insights_updated_at
                """,
                (user_id, STALE_PROCESSING_AFTER),
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return None if row is None else row[0]


# The uploader of a document, if they have a patient summary (asked for
# at least once) and no document job left open.
_SUMMARY_TO_REFRESH = """
SELECT u.id FROM documents d JOIN users u ON u.id = d.user_id
WHERE d.id = %s
  AND u.patient_insights_status IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM jobs j JOIN documents o ON o.id = j.document_id
      WHERE o.user_id = u.id AND j.status IN ('queued', 'running'))
"""


def refresh_after_document(job) -> None:
    """
    After a document job (the worker's after_job): rebuild the uploader's
    patient summary, in this thread, if they already have one and no other
    document of theirs is queued or running. A summary someone else is
    building is left to that run.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_SUMMARY_TO_REFRESH, (job.document_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return
    user_id = str(row[0])
    claim = claim_patient_summary(user_id)
    if claim is None:
        logger.info(
            "patient summary refresh skipped: one is being built",
            extra=_obs("summary_refresh_skipped", user_id=user_id,
                       document_id=job.document_id),
        )  # fmt: skip
        return
    logger.info(
        "patient summary refresh after a document",
        extra=_obs("summary_refresh", user_id=user_id, document_id=job.document_id),
    )
    run_patient_summary(user_id, claim)


# The summary's result and failure are written only while the run still holds
# its claim: a run that went quiet for STALE_PROCESSING_AFTER and was taken
# over must not overwrite the newer run's summary or mark it failed. Same
# rule as document jobs (document_jobs: every write conditional on the claim).
_FINISH_SUMMARY = """
UPDATE users
SET patient_insights = %(html)s,
    patient_insights_status = 'completed',
    patient_insights_updated_at = NOW()
WHERE id = %(user_id)s
  AND patient_insights_status = 'processing'
  AND patient_insights_updated_at = %(claim)s
"""

_FAIL_SUMMARY = """
UPDATE users
SET patient_insights_status = 'failed',
    patient_insights_error = %(error)s,
    patient_insights_updated_at = NOW()
WHERE id = %(user_id)s
  AND patient_insights_status = 'processing'
  AND patient_insights_updated_at = %(claim)s
"""


def run_patient_summary(user_id: str, claim: datetime) -> None:
    """
    Background job: the patient summary, built by code from the records of
    every processed document of one user (summary/). Only the overview is
    model-written, and it is shown only if it passes its check.

    `claim` is what claim_patient_summary returned. If another run has taken
    the claim over by the time this one ends, it writes nothing and logs
    summary_claim_lost.
    """
    logger.info(
        "patient summary started", extra=_obs("summary_started", user_id=user_id)
    )
    conn = get_db_connection()
    try:
        from summary.build import build_summary, load_inputs

        inputs = load_inputs(conn, user_id)
        result = build_summary(inputs, now=datetime.now(timezone.utc))
        if result.overview.outcome != "shown":
            check = result.overview.check
            logger.warning(
                "patient summary overview left out",
                extra=_obs(
                    "summary_overview_left_out",
                    user_id=user_id,
                    overview=result.overview.outcome,
                    unknown_numbers=check.unknown_numbers if check else None,
                    unknown_words=check.unknown_words if check else None,
                    attempts=result.overview.attempts,
                ),
            )

        with conn.cursor() as cur:
            cur.execute(
                _FINISH_SUMMARY,
                {"html": result.html, "user_id": user_id, "claim": claim},
            )
            if cur.rowcount == 0:
                raise LostClaim(f"patient summary claim for {user_id} was taken over")
        conn.commit()
        logger.info(
            "patient summary completed",
            extra=_obs(
                "summary_completed",
                user_id=user_id,
                documents=len(result.facts.documents),
                not_included=result.facts.not_included,
                awaiting=result.facts.awaiting,
                overview=result.overview.outcome,
                html_chars=len(result.html),
            ),
        )

    except LostClaim:
        conn.rollback()
        logger.warning(
            "patient summary claim lost; result not written",
            extra=_obs("summary_claim_lost", user_id=user_id),
        )
    except Exception as e:
        logger.exception(
            "patient summary failed", extra=_obs("summary_failed", user_id=user_id)
        )
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    _FAIL_SUMMARY,
                    {"error": str(e) or type(e).__name__, "user_id": user_id,
                     "claim": claim},
                )  # fmt: skip
                lost = cur.rowcount == 0
            conn.commit()
            if lost:
                logger.warning(
                    "patient summary claim lost; failure not written",
                    extra=_obs("summary_claim_lost", user_id=user_id),
                )
        except Exception:
            logger.exception(
                "could not record the failed patient summary",
                extra=_obs("summary_failure_not_recorded", user_id=user_id),
            )
    finally:
        conn.close()
