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
"""

import hmac
import logging

from fastapi import FastAPI, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg

import os


# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s %(name)s - %(message)s",
)
logger = logging.getLogger("insights_service")

# -------------------------------------------------------------------
# FastAPI app + internal auth
# -------------------------------------------------------------------
app = FastAPI(title="Insights Generation Service")

# Shared with the Go backend (same variable name there).
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")
if not INTERNAL_API_TOKEN:
    logger.warning("INTERNAL_API_TOKEN is not set; /internal/* will reject all calls.")


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
        logger.exception(f"[worker] Could not mark document_id={document_id} as failed")


def run_insights_pipeline(document_id: str) -> None:
    """
    Background job that:
      1. Looks up `storage_path` and `extracted_markdown` from DB.
      2. If markdown missing -> runs VLM extraction & saves to DB.
      3. If markdown exists -> skips VLM.
      4. Generates HTML insights via generate_insights_html (SLM).
      5. Saves the result to the DB.
    """
    logger.info(f"[worker] Starting insight generation for document_id={document_id!r}")

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
                f"[worker] Nothing to do for document_id={document_id}: "
                "document not found, or insights already completed/processing."
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
                f"[worker] Document path not found (unexpected): {document_id}"
            )
            mark_insight_failed(conn, document_id, "Document has no stored file path.")
            return

        # The backend stores absolute paths. Older rows hold paths like
        # "uploads/file.jpg", relative to backend/ (where it used to run).
        if not os.path.isabs(input_path):
            from config import PROJECT_ROOT

            input_path = os.path.join(PROJECT_ROOT, "backend", input_path)

        if not os.path.exists(input_path):
            logger.error(f"[worker] File not found on disk: {input_path}")
            mark_insight_failed(conn, document_id, "Uploaded file not found on disk.")
            return

        logger.info("[worker] Using input file path: %s", input_path)

        # 2. Conditional Extraction
        markdown = existing_markdown
        if not markdown:
            logger.info(
                "[worker] No existing markdown found. Running VLM extraction..."
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
                logger.info("[worker] Saved extracted markdown to DB.")
            except Exception as e:
                logger.error(f"[worker] Failed to save extracted markdown: {e}")
                # We continue anyway to generate insights, though it won't be cached
        else:
            logger.info(
                "[worker] Found existing markdown in DB. Skipping VLM extraction."
            )

        # 3. Generate HTML Insights (SLM)
        from generate_insights_txt import generate_insights_html

        touch_insight(conn, document_id)

        html = generate_insights_html(markdown)

        logger.info(
            "[worker] Insights generated for document_id=%s (markdown_len=%d, html_len=%d)",
            document_id,
            len(markdown),
            len(html),
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
                f"[worker] DB updated (Completed) for document_id={document_id!r}"
            )

    except Exception as e:
        logger.exception(
            "[worker] Unhandled exception while generating insights for document_id=%s: %s",
            document_id,
            e,
        )
        if claimed:
            mark_insight_failed(conn, document_id, str(e) or type(e).__name__)
    finally:
        if conn:
            conn.close()

    logger.info(f"[worker] Finished insight generation for document_id={document_id!r}")


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
        f"Received POST /internal/generate-insights for document_id={req.document_id!r}"
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
        f"Received POST /internal/generate-user-insights for user_id={req.user_id!r}"
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
    logger.info(f"[summary] Starting patient summary for user_id={user_id!r}")
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
            f"[summary] Completed for user_id={user_id!r} ({len(rows)} documents)"
        )

    except Exception as e:
        logger.exception(f"[summary] Failed for user_id={user_id!r}")
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
            logger.exception(f"[summary] Could not record failure for {user_id!r}")
    finally:
        conn.close()
