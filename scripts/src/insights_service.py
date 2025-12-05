#!/usr/bin/env python

"""
Insights Generation Service (FastAPI)

- Exposes:
    GET  /health
    OPTIONS /internal/generate-insights
    POST    /internal/generate-insights

- CORS enabled for:
    http://localhost:5173  (frontend)
    http://localhost:8080  (Go backend)

- Flow expected from frontend:
    1) Call Go: GET /documents/{id}/insight
    2) If no insight -> call this service:
          POST /internal/generate-insights { "document_id": "<id>" }
    3) Show "insights are being generated" message

Current behavior of the worker:
    - Uses the shared pipeline in processor_wrapper.process_document(...)
      to:
        1) Extract Markdown from the report (via qwen2.5vl:7b VLM)
        2) Generate HTML insights via your text model (qwen3:4b-instruct)
    - For now, uses INPUT_PDF from config.py as the input file path.
      You should later replace that with a lookup based on document_id.
"""

import logging
from typing import List

from fastapi import FastAPI, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import psycopg2

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
# FastAPI app + CORS
# -------------------------------------------------------------------
app = FastAPI(title="Insights Generation Service")

# Allowed origins: frontend + Go backend
origins: List[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # IMPORTANT: allows OPTIONS, POST, etc.
    allow_headers=["*"],
)


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
    Connects to the Postgres database using the connection string.
    """
    # Connection string from backend/run.ps1
    dsn = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
    return psycopg2.connect(dsn)


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
    try:
        # 0. Check if insights already exist or are being processed
        #    We do this BEFORE fetching file paths to fail fast.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, html_insights FROM insights WHERE document_id = %s",
                (document_id,),
            )
            insight_row = cur.fetchone()
            if insight_row:
                status, html = insight_row
                if status == "completed" and html:
                    logger.info(
                        f"[worker] Insights already completed for document_id={document_id}. Skipping."
                    )
                    return
                if status == "processing":
                    logger.info(
                        f"[worker] Insights already processing for document_id={document_id}. Skipping."
                    )
                    return

            # If no row exists, insert 'processing' state immediately to lock it
            import uuid

            new_insight_id = str(uuid.uuid4())

            # We need user_id for the INSERT. Let's fetch it first.
            cur.execute("SELECT user_id FROM documents WHERE id = %s", (document_id,))
            user_row = cur.fetchone()
            if not user_row:
                logger.error(f"[worker] Document not found in DB: {document_id}")
                return

            user_id = user_row[0]

            try:
                cur.execute(
                    """
                    INSERT INTO insights (id, document_id, user_id, status, created_at, updated_at)
                    VALUES (%s, %s, %s, 'processing', NOW(), NOW())
                    """,
                    (new_insight_id, document_id, user_id),
                )
                conn.commit()
            except psycopg2.IntegrityError:
                # Race condition caught by DB constraint
                conn.rollback()
                logger.info(
                    f"[worker] Race condition: Insight row created by another worker for {document_id}. Skipping."
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
            return

        # Ensure absolute path if stored relatively
        if not os.path.isabs(input_path):
            from config import PROJECT_ROOT

            # The file is stored in backend/uploads, so we need to point there
            # If the path from DB is just "uploads/file.jpg", we need to prepend "backend"
            input_path = os.path.join(PROJECT_ROOT, "backend", input_path)

        if not os.path.exists(input_path):
            logger.error(f"[worker] File not found on disk: {input_path}")
            # Optional: Update status to 'failed' here
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

            markdown = extract_markdown_from_file(input_path)

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

        html = generate_insights_html(markdown)

        logger.info(
            "[worker] Insights generated for document_id=%s (markdown_len=%d, html_len=%d)",
            document_id,
            len(markdown),
            len(html),
        )

        # 4. Save to DB (Update)
        try:
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
        except Exception as db_err:
            logger.exception(
                f"[worker] Database error for document_id={document_id}: {db_err}"
            )
            conn.rollback()

    except Exception as e:
        logger.exception(
            "[worker] Unhandled exception while generating insights for document_id=%s: %s",
            document_id,
            e,
        )
    finally:
        if conn:
            conn.close()

    logger.info(f"[worker] Finished insight generation for document_id={document_id!r}")


# -------------------------------------------------------------------


@app.get("/health")
async def health():
    """Simple health check."""
    return {"status": "ok"}


# Catch-all OPTIONS handler so *no* OPTIONS request 405s
@app.options("/{full_path:path}")
async def options_catch_all(full_path: str):
    """
    Handle any OPTIONS request with 204 so browsers' CORS preflight
    never see a 405.
    """
    logger.info(f"Received CATCH-ALL OPTIONS for path='/{full_path}'")
    return Response(status_code=204)


# Explicit OPTIONS handler for the specific endpoint (optional but clear)
@app.options("/internal/generate-insights")
async def options_generate_insights():
    logger.info("Received OPTIONS for /internal/generate-insights")
    return Response(status_code=204)


@app.post("/internal/generate-insights")
async def generate_insights_endpoint(
    req: GenerateInsightsRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger insight generation for a given document_id.

    Called from frontend when Go /documents/{id}/insight
    has no existing insight.

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


@app.post("/internal/generate-user-insights")
async def generate_user_insights_endpoint(req: GenerateUserInsightsRequest):
    """
    Generate a cumulative summary for all documents of a user.
    """
    logger.info(
        f"Received POST /internal/generate-user-insights for user_id={req.user_id!r}"
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Fetch all documents with extracted markdown for this user
            cur.execute(
                """
                SELECT extracted_markdown, uploaded_at
                FROM documents
                WHERE user_id = %s AND extracted_markdown IS NOT NULL AND extracted_markdown != ''
                ORDER BY uploaded_at ASC
                """,
                (req.user_id,),
            )
            rows = cur.fetchall()

        if not rows:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "No documents with extracted markdown found for this user."
                },
            )

        # Aggregate markdowns
        aggregated_markdown = ""
        for i, (markdown, uploaded_at) in enumerate(rows):
            date_str = (
                uploaded_at.strftime("%Y-%m-%d") if uploaded_at else "Unknown Date"
            )
            aggregated_markdown += f"\n[REPORT {i + 1} - {date_str}]\n{markdown}\n"

        aggregated_markdown += "\n[END OF REPORTS]\n"

        # Generate insights
        from config import PATIENT_SUMMARY_PROMPT_FILE
        from generate_insights_txt import generate_insights_html, load_text

        prompt = load_text(PATIENT_SUMMARY_PROMPT_FILE)
        html = generate_insights_html(aggregated_markdown, prompt=prompt)

        # Save to DB
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET patient_insights = %s WHERE id = %s",
                (html, req.user_id),
            )
            conn.commit()

        return JSONResponse(
            status_code=200,
            content={"status": "completed", "html": html},
        )

    except Exception as e:
        logger.exception(f"Error generating user insights: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )
    finally:
        if conn:
            conn.close()
