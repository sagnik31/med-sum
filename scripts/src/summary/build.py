"""
A patient's summary from their document records: the documents read from
the database, the facts computed, the overview written and checked, the
page built. The service's summary job (insights_service) runs this.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config
from record.document import DocumentRecord
from summary.facts import Facts, SummaryInput, build_facts
from summary.overview import Overview, write_overview
from views.summary import summary_html

OVERVIEW_PROMPT_FILE = (
    Path(config.SCRIPTS_DIR) / "prompt" / "txt" / "summary_overview.txt"
)


class NoDocumentsError(ValueError):
    """The patient has no document with a finished record yet."""


@dataclass(frozen=True)
class Summary:
    html: str
    facts: Facts
    overview: Overview


def load_inputs(conn, user_id: str) -> list[SummaryInput]:
    """Every document of the user, with its processing status and record (if any)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id::text, d.original_name, d.uploaded_at, i.status, r.record
            FROM documents d
            LEFT JOIN insights i ON i.document_id = d.id
            LEFT JOIN document_records r ON r.document_id = d.id
            WHERE d.user_id = %s
            ORDER BY d.uploaded_at
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    return [
        SummaryInput(
            document_id=doc_id,
            name=name,
            uploaded_at=uploaded_at,
            status=status,
            record=DocumentRecord.model_validate(record)
            if record is not None
            else None,
        )
        for doc_id, name, uploaded_at, status, record in rows
    ]


def build_summary(
    inputs: list[SummaryInput], *, now: datetime, prompt: str | None = None, chat=None
) -> Summary:
    facts = build_facts(inputs)
    if not facts.documents:
        raise NoDocumentsError(
            "No processed documents yet. Wait until at least one document is ready."
        )
    if prompt is None:
        prompt = OVERVIEW_PROMPT_FILE.read_text(encoding="utf-8")
    overview = write_overview(facts, prompt, chat=chat)
    return Summary(summary_html(facts, overview, generated_at=now), facts, overview)
