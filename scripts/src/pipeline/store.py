"""
Saving a document's record (document_records), one per document: a new
run's record replaces the last one. Before it is saved, every item must
trace back to a step of the run that produced it (check_provenance); a gap
is a bug in the pipeline, so nothing is saved and the run fails.
"""

import psycopg
from psycopg.types.json import Jsonb

import settings

from observability import check_provenance
from record.document import DocumentRecord


class ProvenanceGapError(RuntimeError):
    """A record item doesn't trace back to its step."""


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url(), autocommit=True)


def save_record(
    record: DocumentRecord, *, run_id: str, claim=None, connect=connect
) -> None:
    """
    `claim` is the run's job claim (Run.claim), checked in the transaction
    of the write: a worker that lost its job never replaces the record of
    the worker that took it over. See docs/adr/0006-durable-traceable-runs.md.
    """
    with connect() as conn:
        problems = check_provenance(conn, record, run_id=run_id)
        if problems:
            # Paths and ids only (check_provenance's promise), so it can be
            # stored as the run's error.
            raise ProvenanceGapError("; ".join(problems))
        with conn.transaction(), conn.cursor() as cur:
            if claim is not None:
                claim.check(cur)
            cur.execute(
                "INSERT INTO document_records"
                " (document_id, schema_version, record, run_id)"
                " VALUES (%(doc)s, %(version)s, %(record)s, %(run)s)"
                " ON CONFLICT (document_id) DO UPDATE SET"
                " schema_version = EXCLUDED.schema_version,"
                " record = EXCLUDED.record, run_id = EXCLUDED.run_id,"
                " updated_at = now()",
                {
                    "doc": record.document.document_id,
                    "version": record.schema_version,
                    "record": Jsonb(record.model_dump(mode="json")),
                    "run": run_id,
                },
            )


# -------------------------------------------------------------------
# Transcriptions of earlier runs
# -------------------------------------------------------------------
_EARLIER = (
    "SELECT s.id, s.model, s.model_role, s.output FROM pipeline_steps s"
    " JOIN pipeline_runs r ON r.id = s.run_id"
    " WHERE r.document_id = %(doc)s AND s.node = 'transcribe'"
    " AND s.status = 'ok' AND s.model = %(model)s AND s.model_role = 'primary'"
    " AND s.prompt_hash = %(prompt)s AND s.input -> 'image' ->> 'sha256' = %(sha)s"
    " AND s.run_id <> %(run)s"
    # Only a reading made by its own calls (not itself a reuse), every call
    # with the request this run would send and, when both are known, the
    # same weights. One whose prompt may have been cut is never reused: its
    # LC-18 comes from the call, and a reuse makes no call.
    " AND NOT (s.output ? 'reused_from_step')"
    # Nor one whose reply went on after the end marker (LC-21 comes from
    # the call, and a reuse makes no call).
    " AND NOT (s.output ? 'stop_marker_in_reply')"
    " AND EXISTS (SELECT 1 FROM pipeline_model_calls c WHERE c.step_id = s.id)"
    " AND NOT EXISTS (SELECT 1 FROM pipeline_model_calls c"
    " WHERE c.step_id = s.id AND (c.prompt_truncated"
    " OR (%(request)s::jsonb IS NOT NULL"
    " AND c.request IS DISTINCT FROM %(request)s::jsonb)"
    " OR (%(digest)s::text IS NOT NULL AND c.model_digest IS NOT NULL"
    " AND c.model_digest <> %(digest)s)))"
    " ORDER BY s.ended_at DESC LIMIT 1"
)


def earlier_transcription(
    document_id: str,
    *,
    image_sha256: str,
    prompt_hash: str,
    model: str,
    run_id: str,
    request: dict | None = None,
    digest: str | None = None,
    connect=connect,
):
    """
    The latest earlier transcription of this page of this document by the
    same model and prompt, as (step id, lines), or None. Only steps that
    ended ok count, so a capped page or a fallback model's reading is read
    again, and so is one whose prompt may have been cut short (LC-18).
    `request` is what this run's call would record (llm.request_of), and
    `digest` the model's weights now (None when unknown): a reading made
    with other options or other weights under the same tag is read again. The image hash ties it to the page's pixels, not its number.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            _EARLIER,
            {
                "doc": document_id,
                "model": model,
                "prompt": prompt_hash,
                "sha": image_sha256,
                "run": run_id,
                "request": Jsonb(request) if request is not None else None,
                "digest": digest,
            },
        )
        row = cur.fetchone()
    if row is None:
        return None
    step_id, _model, _role, output = row
    try:
        pages = (output or {}).get("transcriptions") or []
        lines = pages[0]["lines"]
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        return None
    return str(step_id), lines
