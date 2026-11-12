#!/usr/bin/env python

"""
Insights Generation Service (FastAPI)

- Exposes:
    GET  /health
    POST /internal/generate-insights        (queue a document's job)
    POST /internal/reprocess-document       (queue it again, as a new run)
    POST /internal/generate-user-insights   (background, per user)

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

from fastapi import FastAPI, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
import psycopg

import os

import config
import document_jobs
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
        worker = document_jobs.Worker()
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
    """
    Connects to Postgres using DATABASE_URL (same variable as the Go backend),
    falling back to the local dev database.
    """
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable",
    )
    return psycopg.connect(dsn)


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
    """Simple health check."""
    return {"status": "ok"}


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

    # Claim the job the same way as document insights: skip it while a live
    # run is in progress, but take over one that has gone stale.
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
                RETURNING id
                """,
                (req.user_id, STALE_PROCESSING_AFTER),
            )
            claimed = cur.fetchone() is not None
        conn.commit()
    finally:
        conn.close()

    if claimed:
        background_tasks.add_task(run_patient_summary, req.user_id)
    return {"status": "accepted" if claimed else "already_processing"}


def run_patient_summary(user_id: str) -> None:
    """Background job: summarise every processed document of one user."""
    logger.info(
        "patient summary started", extra=_obs("summary_started", user_id=user_id)
    )
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT extracted_markdown, uploaded_at
                FROM documents
                WHERE user_id = %s AND extracted_markdown IS NOT NULL AND extracted_markdown != ''
                ORDER BY uploaded_at ASC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

        if not rows:
            raise ValueError(
                "No processed documents yet. Wait until at least one document is ready."
            )

        aggregated_markdown = ""
        for i, (markdown, uploaded_at) in enumerate(rows):
            date_str = (
                uploaded_at.strftime("%Y-%m-%d") if uploaded_at else "Unknown Date"
            )
            aggregated_markdown += f"\n[REPORT {i + 1} - {date_str}]\n{markdown}\n"
        aggregated_markdown += "\n[END OF REPORTS]\n"

        from config import PATIENT_SUMMARY_PROMPT_FILE
        from generate_insights_txt import generate_insights_html, load_text

        prompt = load_text(PATIENT_SUMMARY_PROMPT_FILE)
        html = generate_insights_html(aggregated_markdown, prompt=prompt)

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET patient_insights = %s,
                    patient_insights_status = 'completed',
                    patient_insights_updated_at = NOW()
                WHERE id = %s
                """,
                (html, user_id),
            )
        conn.commit()
        logger.info(
            "patient summary completed",
            extra=_obs("summary_completed", user_id=user_id, documents=len(rows)),
        )

    except Exception as e:
        logger.exception(
            "patient summary failed", extra=_obs("summary_failed", user_id=user_id)
        )
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET patient_insights_status = 'failed',
                        patient_insights_error = %s,
                        patient_insights_updated_at = NOW()
                    WHERE id = %s
                    """,
                    (str(e) or type(e).__name__, user_id),
                )
            conn.commit()
        except Exception:
            logger.exception(
                "could not record the failed patient summary",
                extra=_obs("summary_failure_not_recorded", user_id=user_id),
            )
    finally:
        conn.close()
