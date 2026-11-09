#!/usr/bin/env python

"""
Insights Generation Service (FastAPI)

- Exposes:
    GET  /health
    POST /internal/generate-insights        (background, per document)
    POST /internal/generate-user-insights   (background, per user)

- Internal only: /internal/* requires the X-Internal-Token header to match
  INTERNAL_API_TOKEN. No CORS, so browsers cannot call it; the Go backend
  checks document ownership and forwards requests here.

- Flow:
    1) Frontend calls Go: GET /documents/{id}/insight
    2) If no insight -> frontend calls Go: POST /documents/{id}/insight,
       which calls POST /internal/generate-insights { "document_id": "<id>" }
    3) Frontend shows "insights are being generated" message

Current behavior of the worker:
    - Looks up the document's storage_path in the DB (an absolute path written by the backend)
    - Extracts Markdown from page images, cached in documents.extracted_markdown
    - Generates HTML insights from that Markdown
    - Both steps use the primary model, with a fallback (see llm.py)

- Logs: structured JSON lines on stderr and in config.LOG_FILE, set up when
  the app starts (observability.configure_logging). They carry ids, events
  and sizes, never document content or exception messages.
"""

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg

import os

import config
from observability import configure_logging


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(log_file=config.LOG_FILE)
    logger.info("service started", extra=_obs("service_started"))
    if not INTERNAL_API_TOKEN:
        logger.warning(
            "INTERNAL_API_TOKEN is not set; /internal/* will reject all calls",
            extra=_obs("internal_token_missing"),
        )
    yield


app = FastAPI(title="Insights Generation Service", lifespan=lifespan)


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    """Endpoints act on any document/user ID, so only the backend may call them."""
    if not INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=503, detail="INTERNAL_API_TOKEN is not configured"
        )
    if not hmac.compare_digest(x_internal_token.encode(), INTERNAL_API_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")


# A 'processing' job with no heartbeat for this long is treated as abandoned
# and may be claimed again. Heartbeats happen before each PDF page and before
# insight generation, each bounded by the token caps in the model modules.
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


def touch_insight(conn, document_id: str) -> None:
    """Heartbeat: bump updated_at so a live job is not mistaken for a stale one."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE insights SET updated_at = NOW() WHERE document_id = %s AND status = 'processing'",
            (document_id,),
        )
    conn.commit()


def mark_insight_failed(conn, document_id: str, message: str) -> None:
    """Record a failed run so the UI can show it and a later request can retry."""
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE insights
                SET status = 'failed', error_message = %s, updated_at = NOW()
                WHERE document_id = %s
                """,
                (message, document_id),
            )
        conn.commit()
    except Exception:
        logger.exception(
            "could not record the failed document job",
            extra=_obs("job_failure_not_recorded", document_id=document_id),
        )


def run_insights_pipeline(document_id: str) -> None:
    """
    Background job that:
      1. Looks up `storage_path` and `extracted_markdown` from DB.
      2. If markdown missing -> runs VLM extraction & saves to DB.
      3. If markdown exists -> skips VLM.
      4. Generates HTML insights via generate_insights_html (SLM).
      5. Saves the result to the DB.
    """
    logger.info(
        "document job started", extra=_obs("job_started", document_id=document_id)
    )

    conn = get_db_connection()
    claimed = False
    try:
        # 0. Claim the job: create a 'processing' row, or re-claim a 'failed'
        #    one (retry) or a stale 'processing' one (worker died mid-job,
        #    e.g. a --reload restart). Completed and live jobs are left alone.
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO insights (document_id, user_id, status)
                SELECT id, user_id, 'processing' FROM documents WHERE id = %s
                ON CONFLICT (document_id) DO UPDATE
                    SET status = 'processing', error_message = NULL, updated_at = NOW()
                    WHERE insights.status = 'failed'
                       OR (insights.status = 'processing'
                           AND insights.updated_at < NOW() - %s::interval)
                RETURNING id
                """,
                (document_id, STALE_PROCESSING_AFTER),
            )
            claimed = cur.fetchone() is not None
            conn.commit()

        if not claimed:
            logger.info(
                "document job not claimed: no such document, or it is already"
                " completed or being processed",
                extra=_obs("job_not_claimed", document_id=document_id),
            )
            return

        # 1. Get file path and existing markdown
        input_path = None
        existing_markdown = None

        with conn.cursor() as cur:
            cur.execute(
                "SELECT storage_path, extracted_markdown FROM documents WHERE id = %s",
                (document_id,),
            )
            row = cur.fetchone()
            if row:
                input_path = row[0]
                existing_markdown = row[1]

        if not input_path:
            logger.error(
                "document has no stored file path",
                extra=_obs("file_path_missing", document_id=document_id),
            )
            mark_insight_failed(conn, document_id, "Document has no stored file path.")
            return

        # The backend stores absolute paths. Older rows hold paths like
        # "uploads/file.jpg", relative to backend/ (where it used to run).
        if not os.path.isabs(input_path):
            from config import PROJECT_ROOT

            input_path = os.path.join(PROJECT_ROOT, "backend", input_path)

        if not os.path.exists(input_path):
            logger.error(
                "uploaded file not found on disk",
                extra=_obs("file_missing", document_id=document_id),
            )
            mark_insight_failed(conn, document_id, "Uploaded file not found on disk.")
            return

        # 2. Conditional Extraction
        markdown = existing_markdown
        if not markdown:
            logger.info(
                "no cached transcription; transcribing the pages",
                extra=_obs("transcription_started", document_id=document_id),
            )
            # Run extraction only
            from extract_report_slm import extract_markdown_from_file

            markdown = extract_markdown_from_file(
                input_path, on_page=lambda: touch_insight(conn, document_id)
            )

            # Save extracted markdown back to DB
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE documents SET extracted_markdown = %s, updated_at = NOW() WHERE id = %s",
                        (markdown, document_id),
                    )
                    conn.commit()
                logger.info(
                    "transcription saved",
                    extra=_obs(
                        "transcription_saved",
                        document_id=document_id,
                        markdown_chars=len(markdown),
                    ),
                )
            except Exception:
                # Logged by type only: the message could quote the document.
                logger.exception(
                    "could not save the transcription; going on without caching it",
                    extra=_obs("transcription_not_saved", document_id=document_id),
                )
                conn.rollback()
        else:
            logger.info(
                "using the cached transcription",
                extra=_obs("transcription_cached", document_id=document_id),
            )

        # 3. Generate HTML Insights (SLM)
        from generate_insights_txt import generate_insights_html

        touch_insight(conn, document_id)

        html = generate_insights_html(markdown)

        logger.info(
            "insight HTML generated",
            extra=_obs(
                "insights_generated",
                document_id=document_id,
                markdown_chars=len(markdown),
                html_chars=len(html),
            ),
        )

        # 4. Save to DB (Update)
        with conn.cursor() as cur:
            update_query = """
                UPDATE insights
                SET html_insights = %s, status = 'completed', updated_at = NOW()
                WHERE document_id = %s
            """
            cur.execute(update_query, (html, document_id))
            conn.commit()
            logger.info(
                "document job completed",
                extra=_obs("job_completed", document_id=document_id),
            )

    except Exception as e:
        # The message goes to the database only; the log gets its type.
        logger.exception(
            "document job failed", extra=_obs("job_failed", document_id=document_id)
        )
        if claimed:
            mark_insight_failed(conn, document_id, str(e) or type(e).__name__)
    finally:
        if conn:
            conn.close()

    logger.info(
        "document job finished", extra=_obs("job_finished", document_id=document_id)
    )


# -------------------------------------------------------------------


@app.get("/health")
async def health():
    """Simple health check."""
    return {"status": "ok"}


@app.post("/internal/generate-insights", dependencies=[Depends(require_internal_token)])
async def generate_insights_endpoint(
    req: GenerateInsightsRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger insight generation for a given document_id.

    Called by the Go backend (POST /documents/{id}/insight) after it has
    checked that the document belongs to the requesting user.

    Returns quickly; work is done in the background task.
    """
    logger.info(
        "document job requested",
        extra=_obs("job_requested", document_id=req.document_id),
    )

    # Schedule background worker
    background_tasks.add_task(run_insights_pipeline, req.document_id)

    return JSONResponse(
        status_code=200,
        content={"status": "accepted", "document_id": req.document_id},
    )


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
