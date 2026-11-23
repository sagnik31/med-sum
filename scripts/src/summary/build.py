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
from record.review import reviewed_view
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


def load_inputs(conn, patient_id: str) -> list[SummaryInput]:
    """
    Every live document of the patient, with its processing status and
    record (if any). A deleted document is left out. A file uploaded again
    (the same SHA-256) is left out too, so its values don't count twice in
    trends: one copy stands for the file, the earliest that finished
    processing (checks_passed, verified or needs_review), else the earliest. So a copy
    that failed doesn't hide a later one that worked.
    See docs/adr/0008-patient-data-protection.md.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, original_name, uploaded_at, status, record FROM (
                SELECT d.id::text AS id, d.original_name, d.uploaded_at,
                       i.status, r.record,
                       row_number() OVER (
                           PARTITION BY coalesce(d.sha256, d.id::text)
                           ORDER BY coalesce(i.status IN ('checks_passed', 'verified',
                                                         'needs_review'),
                                             false) DESC,
                                    d.uploaded_at, d.id
                       ) AS copy
                FROM documents d
                LEFT JOIN insights i ON i.document_id = d.id
                LEFT JOIN document_records r ON r.document_id = d.id
                WHERE d.patient_id = %s AND d.deleted_at IS NULL
            ) live
            WHERE copy = 1
            ORDER BY uploaded_at, id
            """,
            (patient_id,),
        )
        rows = cur.fetchall()
    return [
        SummaryInput(
            document_id=doc_id,
            name=name,
            uploaded_at=uploaded_at,
            status=status,
            # As a clinician left it: corrected values in place.
            # See docs/adr/0011-clinician-review.md.
            record=reviewed_view(DocumentRecord.model_validate(record))
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
