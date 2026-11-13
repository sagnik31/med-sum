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


def save_record(record: DocumentRecord, *, run_id: str, connect=connect) -> None:
    with connect() as conn:
        problems = check_provenance(conn, record, run_id=run_id)
        if problems:
            # Paths and ids only (check_provenance's promise), so it can be
            # stored as the run's error.
            raise ProvenanceGapError("; ".join(problems))
        with conn.transaction(), conn.cursor() as cur:
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
    " ORDER BY s.ended_at DESC LIMIT 1"
)


def earlier_transcription(
    document_id: str,
    *,
    image_sha256: str,
    prompt_hash: str,
    model: str,
    run_id: str,
    connect=connect,
):
    """
    The latest earlier transcription of this page of this document by the
    same model and prompt, as (step id, lines), or None. Only steps that
    ended ok count, so a capped page or a fallback model's reading is read
    again. The image hash ties it to the page's pixels, not its number.
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
