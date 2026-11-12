"""
The pipeline's nodes, one at a time, inside a pipeline run, with the fake
model client: what each node returns, what its step stores, and what it
sends to the model. The synthetic dataset supplies the files, and its
answer keys stand in for the model's replies.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

import pymupdf
import pytest

import llm
from dataset.build import DATASET_DIR
from dataset_records import DOCS, PARSED_IDS, expected_patient, key, parse_of
from db_rows import add_account
from extract_report_slm import END_MARKER, transcription_messages
from observability import is_traced, start_run
from pipeline import nodes
from pipeline import pages as page_files
from pipeline.classify import Decision, decide, keyword_check
from pipeline.prompts import FILES, prompt
from pipeline.state import PageImage, initial_state, merge_pages
from record.document import PageTranscription, numbered_document
from record.printed import DocumentType, LabReportParse, PrescriptionParse
from record.schemas import classifier_schema, parse_output_schema
from validators import Severity

LAB, RX, OTHER = DocumentType.LAB_REPORT, DocumentType.PRESCRIPTION, DocumentType.OTHER
TODAY = date(2026, 11, 9)


def file_of(doc_id: str) -> Path:
    return DATASET_DIR / DOCS[doc_id]["file"]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(nodes, "today", lambda: TODAY)


@pytest.fixture
def run(db):
    """An open pipeline run on a fresh documents row; ended as verified."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, 'a.pdf', 'application/pdf', '/x') RETURNING id",
            (add_account(db),),
        )
        document_id = str(cur.fetchone()[0])
    with start_run(document_id, "test-nodes") as r:
        yield r
        if not r.ended:
            r.finish("verified")


def step_rows(db, run) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT node, page, status, model, model_role, prompt_name, prompt_hash,"
            " input, output, findings, error FROM pipeline_steps"
            " WHERE run_id = %s ORDER BY seq",
            (run.id,),
        )
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def opened(doc_id: str, **extra) -> dict:
    """The state after the open node, without running it."""
    path = file_of(doc_id)
    n = page_files.page_count(path)
    state = initial_state(doc_id, file_path=str(path), **extra)
    state["pages_in_file"] = n
    state["pages"] = [
        page_files.describe(i, page_files.page_png(path, i)) for i in range(1, n + 1)
    ]
    return state


def transcribed(doc_id: str, **extra) -> dict:
    """The state after every page is transcribed as the answer key has it."""
    state = opened(doc_id, **extra)
    record = key(doc_id)
    layers = [page_files.text_layer(file_of(doc_id), p.page) for p in record.pages]
    state["transcriptions"] = [
        p.model_copy(update={"text_layer": t}) for p, t in zip(record.pages, layers)
    ]
    return state


# -------------------------------------------------------------------
# Every node is traced
# -------------------------------------------------------------------
def test_every_node_is_traced_under_its_own_name():
    assert all(is_traced(fn) for fn in nodes.NODES.values())
    assert {fn.__medsum_node__ for fn in nodes.NODES.values()} == set(nodes.NODES)


def test_every_prompt_file_exists_and_is_not_empty():
    for name in FILES:
        assert prompt(name).strip(), name


# -------------------------------------------------------------------
# Pages of a file
# -------------------------------------------------------------------
def test_an_image_is_one_page_sent_as_its_own_bytes():
    path = file_of("d02")
    data = path.read_bytes()

    assert page_files.page_count(path) == 1
    assert page_files.page_png(path, 1) == data
    image = page_files.describe(1, data)
    assert image.size_bytes == len(data)
    assert image.sha256 == hashlib.sha256(data).hexdigest()
    assert image.width > 0 and image.height > 0
    assert page_files.text_layer(path, 1) is None


def test_a_pdf_page_is_rendered_and_keeps_its_text_layer():
    path = file_of("d09")

    image = page_files.describe(1, page_files.page_png(path, 1))
    assert image.width == round(pymupdf.open(path)[0].rect.width * 200 / 72)
    assert "Sunrise Diagnostics" in page_files.text_layer(path, 1)


def test_a_scanned_pdf_has_no_text_layer():
    assert not DOCS["d03"]["has_text_layer"]
    assert page_files.text_layer(file_of("d03"), 1) is None


def test_rendering_the_same_page_twice_gives_the_same_image():
    path = file_of("d01")
    assert page_files.page_png(path, 2) == page_files.page_png(path, 2)


@pytest.mark.parametrize(
    "name, content, message",
    [
        ("notes.txt", b"hello", "unsupported file type .txt"),
        ("broken.pdf", b"not a pdf", "the PDF can't be read"),
    ],
)
def test_files_that_cannot_be_processed(tmp_path, name, content, message):
    path = tmp_path / name
    path.write_bytes(content)

    with pytest.raises(page_files.DocumentFileError, match=message):
        page_files.page_count(path)


def test_a_missing_file(tmp_path):
    with pytest.raises(page_files.DocumentFileError, match="missing"):
        page_files.page_count(tmp_path / "gone.pdf")


def test_a_pdf_over_the_page_limit(tmp_path):
    path = tmp_path / "long.pdf"
    with pymupdf.open() as pdf:
        for _ in range(page_files.MAX_PAGES + 1):
            pdf.new_page()
        pdf.save(path)

    with pytest.raises(page_files.DocumentFileError, match="the limit is 20"):
        page_files.page_count(path)


# -------------------------------------------------------------------
# open
# -------------------------------------------------------------------
def test_open_counts_and_describes_the_pages(db, run):
    path = file_of("d01")

    out = nodes.open_document(initial_state("d01", file_path=str(path)))

    assert out["pages_in_file"] == 3
    assert [p.page for p in out["pages"]] == [1, 2, 3]
    assert out["pages"][0] == page_files.describe(1, page_files.page_png(path, 1))
    [row] = step_rows(db, run)
    assert row["node"] == "open" and row["status"] == "ok"
    assert row["input"] == {"document_id": "d01"}  # not the path
    assert row["output"]["pages"][0]["sha256"] == out["pages"][0].sha256


def test_open_fails_its_step_on_a_bad_file(db, run, tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("x")

    with pytest.raises(page_files.DocumentFileError):
        nodes.open_document(initial_state("d01", file_path=str(path)))
    [row] = step_rows(db, run)
    assert row["status"] == "failed"
    assert "unsupported file type" in row["error"]


# -------------------------------------------------------------------
# transcribe
# -------------------------------------------------------------------
def test_lines_are_split_as_printed_with_blank_lines_kept():
    assert nodes.lines_of("# Lab\n\nName: X\n| a | b |") == [
        "# Lab", "", "Name: X", "| a | b |"
    ]  # fmt: skip
    assert nodes.lines_of("") == []
    assert nodes.lines_of("  \n ") == []


def test_transcribe_reads_the_next_page_with_its_text_layer(db, run, model_client):
    state = opened("d09")
    model_client.replies[llm.PRIMARY_MODEL] = [
        f"# Sunrise Diagnostics\n\nName: A\n{END_MARKER}\nignored"
    ]

    out = nodes.transcribe(state)

    [page] = out["transcriptions"]
    assert page.page == 1
    assert page.lines == ["# Sunrise Diagnostics", "", "Name: A"]
    assert page.text_layer == page_files.text_layer(file_of("d09"), 1)
    image = page_files.page_png(file_of("d09"), 1)
    assert model_client.calls[0]["messages"] == transcription_messages(
        image, prompt("extract_page")
    )
    [row] = step_rows(db, run)
    assert (row["node"], row["page"], row["status"]) == ("transcribe", 1, "ok")
    assert (row["model"], row["model_role"]) == (llm.PRIMARY_MODEL, "primary")
    assert row["prompt_name"] == "extract_page"
    assert row["input"]["page"] == 1
    assert row["input"]["image"]["sha256"] == state["pages"][0].sha256
    assert row["output"]["transcriptions"][0]["lines"] == page.lines


def test_an_image_page_has_no_text_layer(db, run, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["Rx"]

    [page] = nodes.transcribe(opened("d02"))["transcriptions"]

    assert page.text_layer is None
    assert model_client.calls[0]["messages"][1]["images"] == [
        file_of("d02").read_bytes()
    ]


def test_transcribe_goes_through_the_pages_in_order(db, run, model_client):
    state = opened("d03")
    model_client.replies[llm.PRIMARY_MODEL] = ["page one", "page two"]

    while nodes.next_page(state) is not None:
        out = nodes.transcribe(state)
        state["transcriptions"] = merge_pages(
            state["transcriptions"], out["transcriptions"]
        )

    assert [(p.page, p.lines) for p in state["transcriptions"]] == [
        (1, ["page one"]), (2, ["page two"])
    ]  # fmt: skip
    assert [(r["page"], r["status"]) for r in step_rows(db, run)] == [
        (1, "ok"), (2, "ok")
    ]  # fmt: skip
    with pytest.raises(ValueError, match="already transcribed"):
        nodes.transcribe(state)


def test_a_page_that_changed_since_it_was_counted_fails(db, run, model_client):
    state = opened("d09")
    state["pages"] = [state["pages"][0].model_copy(update={"sha256": "0" * 64})]

    with pytest.raises(page_files.DocumentFileError, match="changed"):
        nodes.transcribe(state)
    assert model_client.calls == []
    assert step_rows(db, run)[0]["status"] == "failed"


def test_both_models_failing_fails_the_transcribe_step(db, run, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("down")]
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("down")]

    with pytest.raises(llm.ModelOutputError):
        nodes.transcribe(opened("d02"))
    [row] = step_rows(db, run)
    assert row["status"] == "failed" and "ModelOutputError" in row["error"]


def test_a_page_capped_on_every_model_is_kept_empty_and_noted(db, run, model_client):
    state = opened("d03")
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [("cut off", "length")]

    out = nodes.transcribe(state)

    assert out["transcriptions"] == [PageTranscription(page=1, lines=[])]
    assert out["capped_pages"] == [1]
    [row] = step_rows(db, run)
    assert (row["status"], row["error"]) == ("failed", nodes.CAPPED)

    # The next page goes on, and the list keeps the first one.
    state["transcriptions"] = out["transcriptions"]
    state["capped_pages"] = out["capped_pages"]
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [("cut off", "length")]
    assert nodes.transcribe(state)["capped_pages"] == [1, 2]


# -------------------------------------------------------------------
# classify: the keyword check and the rule
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", sorted(DOCS))
def test_keywords_name_each_dataset_document_s_type(doc_id):
    text = (DATASET_DIR / DOCS[doc_id]["transcription"]).read_text("utf-8")
    expected = DocumentType(DOCS[doc_id]["type"])

    found = keyword_check(text).type

    assert found == (None if expected is OTHER else expected)


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "| Test | Result | Unit | Reference Range |\n| Hb | 13 | g/dL | 12 - 15 |",
            LAB,
        ),
        ("Rx\nTab. Dolo 650 mg 1-0-1 x 5 days after food", RX),
        ("Findings: lung fields clear. Impression: normal.", None),
        # One signal of each is no opinion.
        ("Specimen: blood. Tab. something", None),
        # "od" and "stat" in ordinary words are not frequencies.
        ("The food was good and the status stable. Rx", None),
    ],
)
def test_keyword_check(text, expected):
    assert keyword_check(text).type == expected


@pytest.mark.parametrize(
    "model, keywords, expected",
    [
        (LAB, LAB, LAB),
        (LAB, None, LAB),
        (RX, None, RX),
        (OTHER, None, OTHER),
        (None, LAB, LAB),
        (None, None, OTHER),
        (OTHER, RX, RX),
        (LAB, RX, OTHER),
        (RX, LAB, OTHER),
    ],
)
def test_the_rule_combining_model_and_keywords(model, keywords, expected):
    decision = decide(model, keywords)
    assert isinstance(decision, Decision) and decision.type is expected
    assert decision.rule


# -------------------------------------------------------------------
# classify: the node
# -------------------------------------------------------------------
def test_classify_uses_the_model_label_with_the_schema(db, run, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ['{"document_type": "lab_report"}']

    out = nodes.classify(transcribed("d09"))

    assert out == {"doc_type": LAB, "classify_disagree": False}
    call = model_client.calls[0]
    assert call["format"] == classifier_schema()
    assert call["messages"][0]["content"] == prompt("classify_document")
    assert "Sunrise Diagnostics" in call["messages"][1]["content"]
    [row] = step_rows(db, run)
    assert row["prompt_name"] == "classify_document"
    assert row["input"] == {"pages": [1]}
    assert row["output"]["doc_type"] == "lab_report"
    assert row["output"]["rule"] == "model label"
    assert row["output"]["keyword_type"] == "lab_report"


def test_classify_reads_a_capped_amount_of_text(db, run, model_client):
    state = transcribed("d01")
    state["transcriptions"] = [
        p.model_copy(update={"lines": p.lines * 50}) for p in state["transcriptions"]
    ]
    model_client.replies[llm.PRIMARY_MODEL] = ['{"document_type": "lab_report"}']

    nodes.classify(state)

    assert len(model_client.calls[0]["messages"][1]["content"]) == nodes.CLASSIFY_CHARS


@pytest.mark.parametrize(
    "replies, expected, error",
    [
        (["not json"], LAB, "ValidationError"),
        (['{"document_type": "discharge_summary"}'], LAB, "ValidationError"),
        ([ConnectionError("down"), ConnectionError("down")], LAB, "ModelOutputError"),
    ],
    ids=["not-json", "label-off-the-list", "no-model"],
)
def test_without_a_usable_label_the_keywords_decide(
    db, run, model_client, replies, expected, error
):
    model_client.replies[llm.PRIMARY_MODEL] = [replies[0]]
    model_client.replies[llm.FALLBACK_MODEL] = replies[1:]

    out = nodes.classify(transcribed("d09"))

    assert out == {"doc_type": expected, "classify_disagree": False}
    [row] = step_rows(db, run)
    assert row["status"] == "ok"
    assert row["output"]["model_error"] == error
    assert row["output"]["model_label"] is None


def test_a_model_saying_other_gives_way_to_clear_keywords(db, run, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ['{"document_type": "other"}']

    assert nodes.classify(transcribed("d02")) == {
        "doc_type": RX,
        "classify_disagree": True,
    }


def test_model_and_keywords_naming_different_types_is_other(db, run, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ['{"document_type": "prescription"}']

    assert nodes.classify(transcribed("d09")) == {
        "doc_type": OTHER,
        "classify_disagree": True,
    }
    assert step_rows(db, run)[0]["output"]["rule"] == "model and keywords disagree"


def test_an_other_document_stays_other(db, run, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ['{"document_type": "other"}']

    assert nodes.classify(transcribed("d17")) == {
        "doc_type": OTHER,
        "classify_disagree": False,
    }


# -------------------------------------------------------------------
# parse
# -------------------------------------------------------------------
def reply_for(doc_id: str) -> str:
    return parse_of(key(doc_id)).model_dump_json()


@pytest.mark.parametrize(
    "doc_id, schema", [("d09", LabReportParse), ("d02", PrescriptionParse)]
)
def test_parse_fills_the_type_s_schema(db, run, model_client, doc_id, schema):
    state = transcribed(doc_id)
    state["doc_type"] = DocumentType(DOCS[doc_id]["type"])
    model_client.replies[llm.PRIMARY_MODEL] = [reply_for(doc_id)]

    out = nodes.parse(state)

    assert isinstance(out["parse"], schema)
    assert out["parse"] == parse_of(key(doc_id))
    assert out["parse_error"] is None
    assert out["parse_attempts"] == 1
    call = model_client.calls[0]
    assert call["format"] == parse_output_schema(state["doc_type"])
    assert call["messages"][1]["content"] == numbered_document(state["transcriptions"])
    assert call["options"]["num_predict"] == nodes.PARSE_MAX_TOKENS
    [row] = step_rows(db, run)
    assert row["status"] == "ok"
    assert row["prompt_name"] == f"parse_{state['doc_type'].value}"
    assert row["input"] == {
        "doc_type": state["doc_type"].value, "model_role": "primary",
        "attempt": 1, "pages": [p.page for p in state["transcriptions"]],
    }  # fmt: skip


def test_parse_on_the_fallback_calls_only_the_fallback(db, run, model_client):
    state = transcribed("d09")
    state.update(doc_type=LAB, model_role="fallback", parse_attempts=3)
    model_client.replies[llm.FALLBACK_MODEL] = [reply_for("d09")]

    out = nodes.parse(state)

    assert out["parse_attempts"] == 4
    assert [c["model"] for c in model_client.calls] == [llm.FALLBACK_MODEL]
    row = step_rows(db, run)[0]
    assert (row["model"], row["model_role"]) == (llm.FALLBACK_MODEL, "fallback")


def _document_remark_with_a_test_name(doc_id: str) -> str:
    reply = json.loads(reply_for(doc_id))
    reply["remarks"].append({
        "citation": {"page": 1, "first_line": 1, "last_line": 1, "quote": "x"},
        "text": "x", "kind": "advice", "scope": "document",
        "test_name": "Hb", "panel": None,
    })  # fmt: skip
    return json.dumps(reply)


@pytest.mark.parametrize(
    "reply, expected",
    [
        ('{"patient":', "Invalid JSON"),
        ('{"patient": null}', "patient: Input should be"),
        (None, "Value error, test_name is only set when scope is test"),
    ],
    ids=["cut-off-json", "off-schema", "cross-field-rule"],
)
def test_a_reply_that_breaks_the_schema_fails_the_step_without_raising(
    db, run, model_client, reply, expected
):
    state = transcribed("d02")
    state["doc_type"] = RX
    reply = reply or _document_remark_with_a_test_name("d02")
    model_client.replies[llm.PRIMARY_MODEL] = [reply]

    out = nodes.parse(state)

    assert out["parse"] is None
    assert expected in out["parse_error"]
    assert out["parse_attempts"] == 1
    [row] = step_rows(db, run)
    assert row["status"] == "failed"
    assert row["error"] == out["parse_error"]
    assert row["output"] == {"reply": reply}


def test_a_parse_capped_on_every_model_fails_the_step_without_raising(
    db, run, model_client
):
    state = transcribed("d09")
    state["doc_type"] = LAB
    model_client.replies[llm.PRIMARY_MODEL] = [('{"patient":', "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [('{"patient":', "length")]

    out = nodes.parse(state)

    assert out == {"parse": None, "parse_error": nodes.CAPPED, "parse_attempts": 1}
    [row] = step_rows(db, run)
    assert (row["status"], row["error"]) == ("failed", nodes.CAPPED)


def test_a_parse_with_no_model_answering_raises(db, run, model_client):
    state = transcribed("d09")
    state["doc_type"] = LAB
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("down")]
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("down")]

    with pytest.raises(llm.ModelOutputError):
        nodes.parse(state)


def test_schema_errors_name_where_and_why_without_the_values(db, run, model_client):
    state = transcribed("d09")
    state["doc_type"] = LAB
    reply = json.loads(reply_for("d09"))
    reply["results"][0]["value_text"] = ""
    model_client.replies[llm.PRIMARY_MODEL] = [json.dumps(reply)]

    error = nodes.parse(state)["parse_error"]

    assert error == "results.0.value_text: String should have at least 1 character"


def test_a_document_of_type_other_is_not_parsed(db, run, model_client):
    state = transcribed("d17")
    state["doc_type"] = OTHER

    with pytest.raises(ValueError, match="not parsed"):
        nodes.parse(state)
    assert model_client.calls == []


# -------------------------------------------------------------------
# validate
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", PARSED_IDS)
def test_validating_a_dataset_key_s_parse_finds_no_errors_or_flags(db, run, doc_id):
    state = transcribed(doc_id, expected_patient=expected_patient(doc_id))
    state["doc_type"] = DocumentType(DOCS[doc_id]["type"])
    state["parse"] = parse_of(key(doc_id))

    out = nodes.validate(state)

    record = out["record"]
    assert record.document.has_text_layer is DOCS[doc_id]["has_text_layer"]
    assert record.observations == [
        o.model_copy(update={"provenance": None}) for o in key(doc_id).observations
    ]
    flagged = {(f.validator.value, f.field) for f in out["findings"]
               if f.severity is not Severity.INFO}  # fmt: skip
    # d16 is printed for another patient (see test_validators).
    assert flagged == ({("patient_match", "name"), ("patient_match", "age")}
                       if doc_id == "d16" else set())  # fmt: skip
    [row] = step_rows(db, run)
    assert row["status"] == "ok"
    assert len(row["findings"]) == len(out["findings"])
    assert "pages" not in row["output"]["record"]
    assert row["input"]["pages"] == [p.page for p in state["transcriptions"]]


def test_findings_are_stored_with_the_validate_step(db, run):
    state = transcribed("d09", expected_patient=expected_patient("d09"))
    state["doc_type"] = LAB
    parse = parse_of(key("d09"))
    parse.results[0].value_text = "99.9"
    state["parse"] = parse

    out = nodes.validate(state)

    errors = [f for f in out["findings"] if f.severity is Severity.ERROR]
    assert errors and errors[0].item_id == "obs-1"
    stored = step_rows(db, run)[0]["findings"]
    assert any(f["severity"] == "error" and f["item_id"] == "obs-1" for f in stored)


def test_the_uploader_is_kept_on_the_record(db, run):
    state = transcribed("d02", uploaded_by="Dr Test")
    state.update(doc_type=RX, parse=parse_of(key("d02")))

    assert nodes.validate(state)["record"].document.uploaded_by == "Dr Test"


def test_a_document_of_type_other_gets_a_record_of_its_pages(db, run):
    state = transcribed("d17")
    state["doc_type"] = OTHER

    out = nodes.validate(state)

    assert out["record"].pages == state["transcriptions"]
    assert out["record"].observations == []
    assert out["findings"] == []


def test_a_parsed_type_without_a_parse_cannot_be_validated(db, run):
    state = transcribed("d09")
    state["doc_type"] = LAB

    with pytest.raises(ValueError, match="no parse"):
        nodes.validate(state)


# -------------------------------------------------------------------
# render
# -------------------------------------------------------------------
def test_render_makes_the_insight_page_from_the_transcription(db, run, model_client):
    state = transcribed("d09")
    model_client.replies[llm.PRIMARY_MODEL] = [
        "<h2>Summary</h2><script>alert(1)</script>"
    ]

    html = nodes.render(state)["insight_html"]

    assert "<h2>Summary</h2>" in html and "<script>" not in html
    call = model_client.calls[0]
    assert call["messages"][0]["content"] == prompt("insight_prompt")
    assert call["messages"][1]["content"] == nodes.plain_text(state["transcriptions"])
    [row] = step_rows(db, run)
    assert row["prompt_name"] == "insight_prompt"
    assert row["input"] == {"pages": [1]}


# -------------------------------------------------------------------
# A node outside a run
# -------------------------------------------------------------------
def test_a_node_cannot_run_outside_a_pipeline_run():
    from observability import RunError

    with pytest.raises(RunError):
        nodes.open_document(initial_state("d09", file_path=str(file_of("d09"))))


def test_page_images_in_the_state_are_descriptions_only():
    image = opened("d09")["pages"][0]
    assert isinstance(image, PageImage)
    assert set(image.model_dump()) == {
        "page",
        "sha256",
        "size_bytes",
        "width",
        "height",
    }
    assert PageTranscription.model_fields.keys() >= {"text_layer"}
