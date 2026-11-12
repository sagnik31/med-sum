"""
Saving a document's record (document_records), one per document: a new
run's record replaces the last one. Before it is saved, every item must
trace back to a step of the run that produced it (check_provenance); a gap
is a bug in the pipeline, so nothing is saved and the run fails.
"""

import os

import psycopg
from psycopg.types.json import Jsonb

from observability import check_provenance
from record.document import DocumentRecord


class ProvenanceGapError(RuntimeError):
    """A record item doesn't trace back to its step."""


def connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(dsn, autocommit=True)


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
