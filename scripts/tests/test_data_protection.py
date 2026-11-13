"""
Patient-data protection: every column and log field has a data class,
redaction of what leaves the clinical stores, the cloud-call guard in
llm.chat, and the leak test: the whole dataset through the graph with the
fake model client, after which no dataset identifier is in a log line or a
telemetry column.
"""

import json
import re

import pytest

import llm
from dataset.build import DATASET_DIR
from dataset.content import DOCUMENTS, PATIENTS
from dataset_records import DOCS, expected_patient, key, part_replies
from db_rows import add_account
from evals import redaction
from observability import context, data_classes, logs
from observability.data_classes import COLUMNS, LOG_FIELDS, OUTSIDE_CLINICAL, TELEMETRY
from observability.redact import PATIENT, redact, redact_finding
from pipeline import graph as pipeline_graph
from pipeline import nodes
from pipeline.state import initial_state
from validators.patient import ExpectedPatient

PRIMARY, FALLBACK = llm.PRIMARY_MODEL, llm.FALLBACK_MODEL


# -------------------------------------------------------------------
# Data classes
# -------------------------------------------------------------------
def test_every_column_has_a_class_and_every_class_names_a_column(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c"
            " JOIN information_schema.tables t USING (table_schema, table_name)"
            " WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'"
        )
        found = set(cur.fetchall())
        cur.execute(
            "SELECT DISTINCT table_schema FROM information_schema.tables"
            " WHERE table_schema NOT IN ('public', 'pg_catalog', 'information_schema')"
        )
        schemas = {s for (s,) in cur.fetchall()}
    classified = {(t, c) for t, cols in COLUMNS.items() for c in cols}
    assert found - classified == set(), "columns without a data class"
    assert classified - found == set(), "classes for columns that don't exist"
    assert schemas <= set(data_classes.SCHEMAS)
    assert {k for cols in COLUMNS.values() for k in cols.values()} <= set(
        data_classes.CLASSES
    )


def test_every_log_field_has_a_class_that_may_leave_the_clinical_stores():
    assert set(LOG_FIELDS) == set(logs.LOG_FIELDS)
    assert set(LOG_FIELDS.values()) <= set(OUTSIDE_CLINICAL)


# -------------------------------------------------------------------
# Redaction
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "known", "masked"),
    [
        (
            "Mrs. Meera Iyer does not match",
            ["Meera Iyer"],
            "Mrs. [PATIENT] does not match",
        ),
        ("printed MEERA  IYER", ["Meera Iyer"], "printed [PATIENT]"),
        ("Patient: Iyer, M.", ["Meera Iyer"], "Patient: [PATIENT], M."),
        ("Lab ID SD-25-092011", [], "Lab ID [ID]"),
        ("MRN SH-100231 on page 1", [], "MRN [ID] on page 1"),
        ("call 98765 43210 or +91 9876543210", [], "call [PHONE] or [PHONE]"),
        ("Aadhaar 2345 6789 0123", [], "Aadhaar [AADHAAR]"),
        ("ABHA 91-2345-6789-0123", [], "ABHA [ABHA]"),
        ("PAN ABCDE1234F", [], "PAN [PAN]"),
        ("mail a.b@example.test now", [], "mail [EMAIL] now"),
    ],
)
def test_identifiers_are_masked(text, known, masked):
    assert redact(text, known) == masked


@pytest.mark.parametrize(
    "text",
    [
        "Haemoglobin 13.2 g/dL not on line 4 (LC-02)",
        "run 3f2a9c1e-0b4d-4e6f-8a1b-123456789012 step 7",
        "ValueError: 4096 tokens at 1731487200123 ms",
        "TSH 2.5 mIU/L, 2025-06-01, ref 0.4-4.0",
    ],
)
def test_ordinary_text_is_left_alone(text):
    assert redact(text) == text


def test_a_finding_s_message_and_details_are_masked():
    finding = {
        "validator": "patient",
        "message": "printed Meera Iyer, expected Rohan Kulkarni",
        "details": {"printed": "Meera Iyer", "lines": "3"},
    }
    out = redact_finding(finding, ["Meera Iyer", "Rohan Kulkarni"])
    assert out["message"] == f"printed {PATIENT}, expected {PATIENT}"
    assert out["details"] == {"printed": PATIENT, "lines": "3"}
    assert finding["message"].startswith("printed Meera")  # not changed in place


def test_redaction_recall_with_the_run_s_known_values_is_complete():
    result = redaction.measure()
    assert all(r["with_known"] == r["total"] for r in result.values())
    for kind in ("hospital_id", "phone", "aadhaar", "abha", "pan", "email"):
        assert result[kind]["patterns_only"] == result[kind]["total"], kind
    assert "| printed_name |" in redaction.report(result)


# -------------------------------------------------------------------
# The cloud-call guard
# -------------------------------------------------------------------
TEXT = [{"role": "user", "content": "Parse this."}]
IMAGE = [{"role": "user", "content": "Read this page.", "images": [b"png"]}]


@pytest.mark.parametrize(
    "model", ["gemini-2.5-flash", "gpt-4o", "claude-x", "gpt-oss:120b-cloud"]
)
def test_cloud_models_are_recognised(model):
    assert llm.is_cloud(model)


def test_local_and_listed_models():
    assert not llm.is_cloud("qwen3.5:4b") and not llm.is_cloud("gemma4:e2b")
    assert llm.is_cloud("my-model", environ={"MEDSUM_CLOUD_MODELS": "x, my-model"})


@pytest.fixture
def cloud_primary(monkeypatch, model_client):
    monkeypatch.setitem(llm._MODELS, "primary", "gemini-test")
    model_client.replies["gemini-test"] = ["from the cloud"]
    return model_client


def test_a_cloud_model_is_not_sent_a_document_not_marked_synthetic(cloud_primary):
    cloud_primary.replies[FALLBACK] = ["local"]
    assert llm.chat(TEXT, max_tokens=10) == "local"
    assert [c["model"] for c in cloud_primary.calls] == [FALLBACK]


def test_a_cloud_model_is_never_sent_a_page_image(cloud_primary, monkeypatch):
    monkeypatch.setitem(llm._MODELS, "fallback", "gemini-test")
    with context.synthetic_documents():
        with pytest.raises(llm.ModelUnavailableError, match="page images"):
            llm.chat(IMAGE, max_tokens=10)
    assert cloud_primary.calls == []


def test_synthetic_text_may_go_to_a_cloud_model(cloud_primary):
    with context.synthetic_documents():
        assert llm.chat(TEXT, max_tokens=10) == "from the cloud"
    assert context.synthetic_only.get() is False


# -------------------------------------------------------------------
# The leak test
# -------------------------------------------------------------------
def dataset_identifiers() -> set[str]:
    values = set()
    for p in PATIENTS:
        values |= {p.name, p.mrn, p.dob.isoformat(), p.dob.strftime("%d/%m/%Y")}
    for d in DOCUMENTS:
        patient = key(d.id).patient
        if patient is not None:
            for field in ("name", "patient_id", "date_of_birth"):
                value = getattr(patient.printed, field)
                if value is not None:
                    values.add(value.text)
    # A name's parts too: "Iyer" alone still names her.
    parts = {w for v in list(values) for w in re.findall(r"[A-Za-z]{4,}", v)}
    return values | (parts - {"Mrs", "Miss", "Master"})


def replies(doc_id: str) -> list[str]:
    out = ["\n".join(p.lines) for p in key(doc_id).pages]
    out.append(json.dumps({"document_type": DOCS[doc_id]["type"]}))
    if DOCS[doc_id]["type"] != "other":
        out.extend(part_replies(doc_id))
    return out


def run_doc(db, doc_id, model_client, expected=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type,"
            " storage_path) VALUES (%s, 'a', 'application/pdf', '/x') RETURNING id",
            (add_account(db, f"{doc_id}-{len(model_client.calls)}@hospital.test"),),
        )
        document_id = str(cur.fetchone()[0])
    model_client.replies[PRIMARY] = replies(doc_id) * 3  # room for repairs
    model_client.replies[FALLBACK] = replies(doc_id) * 3
    state = initial_state(
        document_id,
        file_path=str(DATASET_DIR / DOCS[doc_id]["file"]),
        uploaded_by="Dr Test",
        expected_patient=expected or expected_patient(doc_id),
    )
    return pipeline_graph.run_document(state)["end_state"]


def telemetry_text(db) -> dict[str, str]:
    out = {}
    with db.cursor() as cur:
        for table, column in data_classes.columns_of(TELEMETRY):
            cur.execute(f'SELECT string_agg("{column}"::text, chr(10)) FROM {table}')
            out[f"{table}.{column}"] = cur.fetchone()[0] or ""
    return out


@pytest.fixture(autouse=False)
def fixed_today(monkeypatch):
    from datetime import date

    monkeypatch.setattr(nodes, "today", lambda: date(2026, 11, 9))


def test_no_dataset_identifier_reaches_a_log_or_a_telemetry_column(
    db, model_client, json_log, fixed_today
):
    ends = {d.id: run_doc(db, d.id, model_client) for d in DOCUMENTS}
    # A document uploaded for the wrong patient: its findings name both.
    wrong = ExpectedPatient(name=PATIENTS[1].name, mrn=PATIENTS[1].mrn)
    ends["d01-wrong"] = run_doc(db, "d01", model_client, expected=wrong)
    assert list(ends.values()).count("failed") == 0, ends

    with db.cursor() as cur:  # the wrong-patient finding was stored, masked
        cur.execute(
            "SELECT count(*) FROM pipeline_steps"
            " WHERE findings::text LIKE %s AND node = 'validate'",
            (f"%{PATIENT}%",),
        )
        assert cur.fetchone()[0] >= 1

    needles = dataset_identifiers()
    places = {"logs": json_log.getvalue(), **telemetry_text(db)}
    leaks = {
        place: sorted(n for n in needles if n.lower() in text.lower())
        for place, text in places.items()
    }
    assert {p: n for p, n in leaks.items() if n} == {}
