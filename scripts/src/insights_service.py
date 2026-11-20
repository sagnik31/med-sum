#!/usr/bin/env python

"""
Insights Generation Service (FastAPI)

- Exposes:
    GET  /health
    POST /internal/generate-insights        (queue a document's job)
    POST /internal/reprocess-document       (queue it again, as a new run)
    POST /internal/generate-patient-summary (background, per patient)
    POST /internal/review-apply             (a clinician's review of a record)

- Patient summaries: built on request, and rebuilt by the worker after a
  document job ends when its patient already has one and has no other
  document queued or running (refresh_after_document), so a batch of
  uploads rebuilds it once, at the end.

- Internal only: /internal/* requires the X-Internal-Token header to match
  INTERNAL_API_TOKEN. No CORS, so browsers cannot call it; the Go backend
  checks the caller's organisation and forwards requests here.

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
from observability.redact import redact
from pipeline import checkpoints
from pydantic import ValidationError
from record.document import DocumentRecord
from record.review import ReviewError, ReviewRequest, apply_review, reviewed_view
from views.document import document_html


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
# Set from settings when the app starts, which refuses to start without a
# strong one; tests that skip the start-up set it themselves.
INTERNAL_API_TOKEN = ""


def worker_enabled() -> bool:
    return os.environ.get("MEDSUM_WORKER", "1") != "0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fails here, at start-up, when DATABASE_URL is missing outside
    # MEDSUM_ENV=dev or INTERNAL_API_TOKEN is missing or short, instead of
    # at the first job or call.
    global INTERNAL_API_TOKEN
    INTERNAL_API_TOKEN = settings.load().internal_api_token
    configure_logging(log_file=config.LOG_FILE)
    logger.info("service started", extra=_obs("service_started"))
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
    """Endpoints act on any document or patient ID, so only the backend may call them."""
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


class GeneratePatientSummaryRequest(BaseModel):
    patient_id: str


class ReviewApplyRequest(BaseModel):
    record: dict
    review: ReviewRequest


class ReviewedViewRequest(BaseModel):
    record: dict


# -------------------------------------------------------------------
# Clinician review
# -------------------------------------------------------------------
@app.post("/internal/review-apply", dependencies=[Depends(require_internal_token)])
def review_apply_endpoint(req: ReviewApplyRequest):
    """
    Apply one clinician review (a confirmation, or a correction of one
    field) to a document's record and return the new record, the checks
    on a corrected value, the item's signal ids, the model's reading of
    the corrected field, how many values still await review, and the
    document page built again from the reviewed record.

    It reads and writes nothing: the Go backend loaded the record (having
    checked the organisation and the permission) and stores the result with
    its audit row in one transaction. See docs/adr/0011-clinician-review.md.
    Nothing here is logged but ids: the record and the values are patient
    data.
    """
    try:
        record = DocumentRecord.model_validate(req.record)
        result = apply_review(record, req.review)
    except ReviewError as exc:
        # The message names ids and fields only (ReviewError's promise).
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValidationError:
        raise HTTPException(
            status_code=422, detail="the record or the review is not valid"
        ) from None
    logger.info(
        "review applied",
        extra=_obs(
            "review_applied",
            document_id=record.document.document_id,
            item_id=req.review.item_id,
            action=req.review.action.value,
            findings=len(result.findings),
            flagged=result.flagged,
        ),
    )
    view = reviewed_view(result.record)
    return {
        "record": result.record.model_dump(mode="json"),
        "html": document_html(view, parsed=view.patient is not None),
        "findings": [
            {
                "validator": f.validator.value,
                "severity": f.severity.value,
                "field": f.field,
                "message": f.message,
            }
            for f in result.findings
        ],
        "signals": result.signals,
        "original": result.original,
        "flagged": result.flagged,
    }


@app.post("/internal/reviewed-view", dependencies=[Depends(require_internal_token)])
def reviewed_view_endpoint(req: ReviewedViewRequest):
    """
    A stored record as clinicians reviewed it: every correction applied and
    the derived values of each corrected item computed again, by the same
    code that built them. The document page's list of values shows this
    view, as its code-built page does (see docs/adr/0011-clinician-review.md).

    Like review-apply it reads and writes nothing: the Go backend sends the
    record it loaded after its own checks. Nothing is logged; the record is
    patient data.
    """
    try:
        view = reviewed_view(DocumentRecord.model_validate(req.record))
    except ValidationError:
        raise HTTPException(status_code=422, detail="the record is not valid") from None
    return {"record": view.model_dump(mode="json")}


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
    "/internal/generate-patient-summary",
    dependencies=[Depends(require_internal_token)],
)
def generate_patient_summary_endpoint(
    req: GeneratePatientSummaryRequest,
    background_tasks: BackgroundTasks,
):
    """
    Queue the cumulative summary across all of a patient's processed
    documents.

    Called by the Go backend (POST /patients/{id}/summary, and after a
    document of the patient is deleted), which checks the organisation.
    Plain `def` (not `async def`) on purpose: the DB call blocks, so FastAPI
    must run this in its threadpool or it freezes the event loop.
    """
    logger.info(
        "patient summary requested",
        extra=_obs("summary_requested", patient_id=req.patient_id),
    )

    claim = claim_patient_summary(req.patient_id)
    if claim is not None:
        background_tasks.add_task(run_patient_summary, req.patient_id, claim)
    # "queued": a live run holds the summary and will build it once more
    # when it ends (claim_patient_summary).
    return {"status": "accepted" if claim is not None else "queued"}


# Whether a request may take the summary: no live run holds it, or the run
# holding it has gone stale (STALE_PROCESSING_AFTER).
_TAKEABLE = "(s.status <> 'processing' OR s.updated_at < NOW() - %(stale)s::interval)"

# One statement, so a run ending at the same moment can't miss the request:
# the row lock orders it before or after the run's own final write.
_CLAIM_SUMMARY = f"""
INSERT INTO patient_summaries AS s (patient_id, status, updated_at)
SELECT id, 'processing', NOW() FROM patients WHERE id = %(patient_id)s
ON CONFLICT (patient_id) DO UPDATE
SET status = 'processing',
    error = NULL,
    rerun_requested = NOT {_TAKEABLE},
    updated_at = CASE WHEN {_TAKEABLE} THEN NOW() ELSE s.updated_at END
RETURNING s.updated_at, s.rerun_requested
"""


def claim_patient_summary(patient_id: str) -> datetime | None:
    """
    Mark the patient's summary processing (its patient_summaries row,
    added on first request), unless a live run has it: one that has gone
    stale (STALE_PROCESSING_AFTER) is taken over.

    Returns the claim (the updated_at it set), or None when a live run
    holds it or there is no such patient. When a live run holds it, the
    request is not dropped: it sets rerun_requested, and that run builds
    the summary once more when it ends, because it read its inputs before
    this request (a correction or a delete may have changed them).
    Nothing else writes updated_at while a run is processing, so the
    timestamp identifies this claim: a takeover sets a new one, at least
    STALE_PROCESSING_AFTER later.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                _CLAIM_SUMMARY,
                {"patient_id": patient_id, "stale": STALE_PROCESSING_AFTER},
            )
            row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if row is None or row[1]:  # no such patient, or queued behind a live run
        return None
    return row[0]


# The patient of a document, if they have a patient summary (asked for at
# least once) and no document job left open.
_SUMMARY_TO_REFRESH = """
SELECT s.patient_id FROM documents d
JOIN patient_summaries s ON s.patient_id = d.patient_id
WHERE d.id = %s
  AND NOT EXISTS (
      SELECT 1 FROM jobs j JOIN documents o ON o.id = j.document_id
      WHERE o.patient_id = d.patient_id AND j.status IN ('queued', 'running'))
"""


def refresh_after_document(job) -> None:
    """
    After a document job (the worker's after_job): rebuild the document's
    patient's summary, in this thread, if they already have one and no
    other document of theirs is queued or running. A summary someone else is
    building is left to that run, which builds it once more when it ends.
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
    patient_id = str(row[0])
    claim = claim_patient_summary(patient_id)
    if claim is None:
        logger.info(
            "patient summary refresh queued behind the one being built",
            extra=_obs("summary_refresh_queued", patient_id=patient_id,
                       document_id=job.document_id),
        )  # fmt: skip
        return
    logger.info(
        "patient summary refresh after a document",
        extra=_obs(
            "summary_refresh", patient_id=patient_id, document_id=job.document_id
        ),
    )
    run_patient_summary(patient_id, claim)


# The summary's result and failure are written only while the run still holds
# its claim: a run that went quiet for STALE_PROCESSING_AFTER and was taken
# over must not overwrite the newer run's summary or mark it failed. Same
# rule as document jobs (document_jobs: every write conditional on the claim).
#
# If a request came in during the run (rerun_requested), the write keeps the
# row processing under a new claim (clock_timestamp: NOW() is when this
# run's transaction began) and the run builds again from fresh inputs; the
# html written meanwhile is shown until then. RETURNING gives the row after
# the write: status 'processing' means build again under that updated_at.
_FINISH_SUMMARY = """
UPDATE patient_summaries
SET html = %(html)s,
    status = CASE WHEN rerun_requested THEN 'processing' ELSE 'completed' END,
    rerun_requested = false,
    updated_at = clock_timestamp()
WHERE patient_id = %(patient_id)s
  AND status = 'processing'
  AND updated_at = %(claim)s
RETURNING status, updated_at
"""

_FAIL_SUMMARY = """
UPDATE patient_summaries
SET status = CASE WHEN rerun_requested THEN 'processing' ELSE 'failed' END,
    error = CASE WHEN rerun_requested THEN NULL ELSE %(error)s END,
    rerun_requested = false,
    updated_at = clock_timestamp()
WHERE patient_id = %(patient_id)s
  AND status = 'processing'
  AND updated_at = %(claim)s
RETURNING status, updated_at
"""


# The patient's identifiers, as registered and as printed on their
# documents, to mask a failure's message with before it is stored.
_SUMMARY_IDENTIFIERS = """
SELECT p.full_name, p.mrn, p.date_of_birth FROM patients p WHERE p.id = %(p)s
UNION ALL
SELECT r.record #>> '{patient,printed,name,text}',
       r.record #>> '{patient,printed,patient_id,text}',
       NULL
FROM document_records r JOIN documents d ON d.id = r.document_id
WHERE d.patient_id = %(p)s
"""


def _summary_identifiers(cur, patient_id: str) -> list[str]:
    """
    The identifiers a summary failure is masked with: the patient's name,
    MRN and date of birth (in the formats documents print), and the name
    and ID printed on each of their documents. Empty if the lookup fails,
    leaving the pattern masking. See docs/adr/0008-patient-data-protection.md.
    """
    try:
        cur.execute(_SUMMARY_IDENTIFIERS, {"p": patient_id})
        rows = cur.fetchall()
    except psycopg.Error:
        cur.connection.rollback()
        return []
    known: list[str] = []
    for name, mrn, dob in rows:
        known += [v for v in (name, mrn) if v]
        if dob is not None:
            known += [
                dob.isoformat(),
                dob.strftime("%d/%m/%Y"),
                dob.strftime("%d-%m-%Y"),
            ]
    return known


def _next_claim(row) -> datetime | None:
    """The new claim when the final write kept the summary processing."""
    return row[1] if row is not None and row[0] == "processing" else None


def run_patient_summary(patient_id: str, claim: datetime) -> None:
    """
    Background job: the patient summary, built by code from the records of
    every processed document of one patient (summary/). Only the overview is
    model-written, and it is shown only if it passes its check.

    `claim` is what claim_patient_summary returned. If another run has taken
    the claim over by the time this one ends, it writes nothing and logs
    summary_claim_lost. If a request came in while it ran, it builds the
    summary again (a request can't wait for it, so this run serves it).
    """
    next_claim = _build_patient_summary(patient_id, claim)
    while next_claim is not None:
        logger.info(
            "patient summary built again: a request came in during the run",
            extra=_obs("summary_rebuild", patient_id=patient_id),
        )
        next_claim = _build_patient_summary(patient_id, next_claim)


def _build_patient_summary(patient_id: str, claim: datetime) -> datetime | None:
    """
    One build under `claim`. Returns the new claim when a request came in
    meanwhile and the summary is to be built again, else None.
    """
    logger.info(
        "patient summary started", extra=_obs("summary_started", patient_id=patient_id)
    )
    next_claim = None
    conn = get_db_connection()
    try:
        from summary.build import build_summary, load_inputs

        inputs = load_inputs(conn, patient_id)
        result = build_summary(inputs, now=datetime.now(timezone.utc))
        if result.overview.outcome != "shown":
            check = result.overview.check
            logger.warning(
                "patient summary overview left out",
                extra=_obs(
                    "summary_overview_left_out",
                    patient_id=patient_id,
                    overview=result.overview.outcome,
                    unknown_numbers=check.unknown_numbers if check else None,
                    unknown_words=check.unknown_words if check else None,
                    attempts=result.overview.attempts,
                ),
            )

        with conn.cursor() as cur:
            cur.execute(
                _FINISH_SUMMARY,
                {"html": result.html, "patient_id": patient_id, "claim": claim},
            )
            if cur.rowcount == 0:
                raise LostClaim(
                    f"patient summary claim for {patient_id} was taken over"
                )
            next_claim = _next_claim(cur.fetchone())
        conn.commit()
        logger.info(
            "patient summary completed",
            extra=_obs(
                "summary_completed",
                patient_id=patient_id,
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
            extra=_obs("summary_claim_lost", patient_id=patient_id),
        )
    except Exception as e:
        logger.exception(
            "patient summary failed",
            extra=_obs("summary_failed", patient_id=patient_id),
        )
        try:
            conn.rollback()
            with conn.cursor() as cur:
                known = _summary_identifiers(cur, patient_id)
                cur.execute(
                    _FAIL_SUMMARY,
                    {"error": redact(str(e) or type(e).__name__, known),
                     "patient_id": patient_id, "claim": claim},
                )  # fmt: skip
                row = cur.fetchone() if cur.rowcount else None
            conn.commit()
            lost = row is None
            next_claim = _next_claim(row)
            if lost:
                logger.warning(
                    "patient summary claim lost; failure not written",
                    extra=_obs("summary_claim_lost", patient_id=patient_id),
                )
        except Exception:
            logger.exception(
                "could not record the failed patient summary",
                extra=_obs("summary_failure_not_recorded", patient_id=patient_id),
            )
    finally:
        conn.close()
    return next_claim
