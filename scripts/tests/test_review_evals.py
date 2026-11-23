"""
The review-based evals against the test database: signal precision and
the escape rate from the views (evals.review_signals), and a reviewed
document exported as a candidate case (evals.export_review). No model.
"""

import json
from datetime import datetime, timezone

import pytest
from psycopg.types.json import Jsonb

from evals import export_review, review_signals
from record.review import apply_review
from test_document_jobs import add_document
from test_review import flagged_record, req

AT = datetime(2025, 3, 5, 10, 0, tzinfo=timezone.utc)


def org_of(db, document_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT p.org_id::text, d.uploaded_by::text FROM documents d"
            " JOIN patients p ON p.id = d.patient_id WHERE d.id = %s",
            (document_id,),
        )
        return cur.fetchone()


def add_run(db, document_id):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_runs (document_id, graph_version, ended_at, end_state)"
            " VALUES (%s, 'test', now(), 'checks_passed') RETURNING id::text",
            (document_id,),
        )
        return cur.fetchone()[0]


def add_review(db, doc, run, item, action, signals, *, audit=False, field="value_text"):
    _, user = org_of(db, doc)
    correct = action == "correct"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO record_reviews (document_id, run_id, item_id, field, action,"
            " original, corrected, signals, audit, reviewer_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (doc, run, item, field if correct else None, action,
             "13.0" if correct else None, "13.9" if correct else None,
             signals, audit, user),
        )  # fmt: skip


# -------------------------------------------------------------------
# Signal precision and the escape rate
# -------------------------------------------------------------------
def test_precision_and_the_escape_rate_come_from_the_reviews(db):
    doc = add_document(db)
    run = add_run(db, doc)
    add_review(db, doc, run, "obs-1", "correct", ["LC-03", "LC-10"])
    add_review(db, doc, run, "obs-2", "confirm", ["LC-03"])
    add_review(db, doc, run, "document", "confirm", ["LC-12"])
    sampled = add_document(db, "d09")
    sampled_run = add_run(db, sampled)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_samples (run_id, document_id, sampled_at, completed_at)"
            " VALUES (%s, %s, now() - interval '1 hour', now() + interval '1 hour')",
            (sampled_run, sampled),
        )
    add_review(db, sampled, sampled_run, "obs-1", "correct", [], audit=True)

    rows = {r.signal: r for r in review_signals.precision(db)}
    assert (rows["LC-03"].reviewed, rows["LC-03"].corrected) == (2, 1)
    assert (rows["LC-10"].reviewed, rows["LC-10"].corrected) == (1, 1)
    assert (rows["LC-12"].reviewed, rows["LC-12"].confirmed) == (1, 1)
    audits = review_signals.escape_rate(db)
    assert (audits.finished, audits.escaped, audits.open) == (1, 1, 0)

    text = review_signals.report(list(rows.values()), audits)
    assert "| LC-03 |" in text and "1/2 (50.0%" in text
    assert "Escape rate: 1/1" in text
    # Counts and signal ids only: no value read or entered.
    assert "13.0" not in text and "13.9" not in text


def test_precision_is_kept_to_one_organisation_when_asked(db):
    doc = add_document(db)
    add_review(db, doc, add_run(db, doc), "obs-1", "confirm", ["LC-03"])
    org, _ = org_of(db, doc)
    assert [r.signal for r in review_signals.precision(db, org)] == ["LC-03"]
    other = "00000000-0000-0000-0000-000000000001"
    assert review_signals.precision(db, other) == []


def test_an_empty_report_says_so():
    text = review_signals.report([], review_signals.EscapeRate(0, 0, 2))
    assert "No reviewed values yet." in text
    assert "No finished audits yet." in text and "2 audits are still waiting" in text


# -------------------------------------------------------------------
# Export as a candidate case
# -------------------------------------------------------------------
def reviewed(db, *, status="verified"):
    """A document whose record a clinician corrected (haemoglobin to 13.9)."""
    doc = add_document(db)
    run = add_run(db, doc)
    result = apply_review(
        flagged_record(), req(field="value_text", value="13.9", note="re-read")
    )
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO insights (document_id, status) VALUES (%s, %s)", (doc, status)
        )
        cur.execute(
            "INSERT INTO document_records (document_id, schema_version, record, run_id)"
            " VALUES (%s, 1, %s, %s)",
            (doc, Jsonb(result.record.model_dump(mode="json")), run),
        )
    add_review(db, doc, run, "obs-1", "correct", ["LC-03", "LC-10"])
    return doc, run


def test_a_reviewed_document_is_exported_with_its_corrected_key(db, tmp_path):
    doc, run = reviewed(db)

    target = export_review.export(db, doc, tmp_path, now=AT)

    assert sorted(p.name for p in target.iterdir()) == [
        "case.json",
        "document.png",
        "key.json",
    ]
    key = json.loads((target / "key.json").read_text(encoding="utf-8"))
    hb = key["observations"][0]
    assert hb["printed"]["value_text"] == "13.9"
    assert hb["corrections"] == []
    case = json.loads((target / "case.json").read_text(encoding="utf-8"))
    assert case["status"] == "candidate" and case["run_id"] == run
    assert case["corrected_fields"] == 1 and case["signals"] == ["LC-03", "LC-10"]
    assert case["reviews"][0]["field"] == "value_text"
    # The case says what changed, not to what.
    assert "13.9" not in (target / "case.json").read_text(encoding="utf-8")


def test_an_export_never_overwrites(db, tmp_path):
    doc, _ = reviewed(db)
    export_review.export(db, doc, tmp_path)
    with pytest.raises(export_review.ExportError, match="exists already"):
        export_review.export(db, doc, tmp_path)


def test_only_a_verified_reviewed_document_is_exported(db, tmp_path):
    doc, _ = reviewed(db, status="needs_review")
    with pytest.raises(export_review.ExportError, match="verified"):
        export_review.export(db, doc, tmp_path)
    unreviewed = add_document(db, "d09")
    with pytest.raises(export_review.ExportError):
        export_review.export(db, unreviewed, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("env", ["", "prod", "staging"])
def test_the_export_is_refused_outside_development(tmp_path, capsys, env):
    code = export_review.main(
        ["00000000-0000-0000-0000-000000000001", str(tmp_path)],
        environ={"MEDSUM_ENV": env},
    )
    assert code == 2
    assert "MEDSUM_ENV=dev" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_the_export_command_writes_the_case_in_development(
    db, database_url, tmp_path, capsys
):
    doc, _ = reviewed(db)
    code = export_review.main(
        [doc, str(tmp_path), "--database-url", database_url],
        environ={"MEDSUM_ENV": "dev"},
    )
    assert code == 0
    assert (tmp_path / doc / "key.json").is_file()
