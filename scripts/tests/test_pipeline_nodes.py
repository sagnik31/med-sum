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
from dataset_records import (
    DOCS,
    MAIN_PARSED_IDS,
    expected_patient,
    key,
    parse_of,
    part_replies,
)
from db_rows import add_account
from observability import is_traced, start_run
from pipeline import nodes, parts
from pipeline import pages as page_files
from pipeline import store
from pipeline.classify import Decision, decide, keyword_check
from pipeline.prompts import (
    END_MARKER,
    FILES,
    MAX_TOKENS_PER_PAGE,
    prompt,
    transcription_messages,
)
from pipeline.state import PageImage, initial_state, merge_pages
from record.common import ItemStatus
from pipeline.parts import PartResult
from record.document import DocumentRecord, PageTranscription
from record.printed import DocumentType, LabReportParse, PrescriptionParse
from record.schemas import classifier_schema, parse_output_schema
from validators import Finding, Severity, Validator
from views.document import document_html

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
            "SELECT node, page, attempt, status, model, model_role, prompt_name,"
            " prompt_hash,"
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
    [call] = model_client.calls
    assert call["messages"] == transcription_messages(image, prompt("extract_page"))
    system, user = call["messages"]
    assert system == {"role": "system", "content": prompt("extract_page")}
    assert user["images"] == [image] and END_MARKER in user["content"]
    assert call["options"]["num_predict"] == MAX_TOKENS_PER_PAGE
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


CUT = ("cut off", "length")  # a reply that hit the token cap


def capped_on_both(model_client, calls: int = 1):
    """The next `calls` transcription calls hit the cap on both models."""
    model_client.replies[llm.PRIMARY_MODEL] = [CUT] * calls
    model_client.replies[llm.FALLBACK_MODEL] = [CUT] * calls


def test_a_page_capped_on_every_model_even_in_halves_is_kept_empty_and_noted(
    db, run, model_client
):
    state = opened("d03")
    capped_on_both(model_client, calls=2)  # the page, then its top half

    out = nodes.transcribe(state)

    assert out["transcriptions"] == [PageTranscription(page=1, lines=[])]
    assert out["capped_pages"] == [1]
    [row] = step_rows(db, run)
    assert row["status"] == "failed"
    assert (
        row["error"]
        == f"{nodes.CAPPED}; read again in halves: a half hit the token cap too"
    )
    assert row["output"]["reread"] == {
        "outcome": "not joined", "half_lines": [],
        "reason": "a half hit the token cap too",
    }  # fmt: skip

    # The next page goes on, and the list keeps the first one.
    state["transcriptions"] = out["transcriptions"]
    state["capped_pages"] = out["capped_pages"]
    capped_on_both(model_client, calls=2)
    assert nodes.transcribe(state)["capped_pages"] == [1, 2]


def test_a_capped_page_is_read_again_in_halves_and_joined(db, run, model_client):
    state = opened("d03")
    top = [
        "# Report",
        "| Test | Result |",
        "| --- | --- |",
        "| Hb | 10.4 |",
        "| MCV | 76 |",
        "| MCH | 25.4 |",
        "| PL",
    ]  # cut through by its edge
    bottom = ["| MCV | 76 |", "| MCH | 25.4 |", "| Platelets | 2.45 |", "End"]
    model_client.replies[llm.PRIMARY_MODEL] = [CUT, "\n".join(top), "\n".join(bottom)]
    model_client.replies[llm.FALLBACK_MODEL] = [CUT]

    out = nodes.transcribe(state)

    assert out["transcriptions"][0].lines == [
        "# Report", "| Test | Result |", "| --- | --- |", "| Hb | 10.4 |",
        "| MCV | 76 |", "| MCH | 25.4 |", "| Platelets | 2.45 |", "End",
    ]  # fmt: skip
    assert "capped_pages" not in out
    [row] = step_rows(db, run)
    assert row["status"] == "ok"
    assert row["output"]["reread"] == {
        "outcome": "joined", "half_lines": [7, 4], "overlap_lines": 2,
    }  # fmt: skip
    # Both attempts are on the step: the capped reading on each model, then
    # each half, sent as a crop of the page image.
    sent = [c["messages"][1]["images"][0] for c in model_client.calls]
    assert len(sent) == 4 and sent[0] == sent[1] != sent[2]
    full = page_files.describe(1, sent[0])
    halves = [page_files.describe(1, image) for image in sent[2:]]
    assert all(h.width == full.width for h in halves)
    assert [h.height for h in halves] == [
        round(full.height * 0.575),
        full.height - round(full.height * 0.425),
    ]


def test_halves_that_share_no_lines_are_not_joined(db, run, model_client):
    state = opened("d03")
    model_client.replies[llm.PRIMARY_MODEL] = [CUT, "a\nb\nc", "x\ny\nz"]
    model_client.replies[llm.FALLBACK_MODEL] = [CUT]

    out = nodes.transcribe(state)

    assert out["capped_pages"] == [1] and out["transcriptions"][0].lines == []
    [row] = step_rows(db, run)
    assert row["error"].endswith("the halves share no single run of lines")


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
    [part] = nodes.parse_parts(state)  # a one-page document is one part
    assert call["messages"][1]["content"] == parts.part_text(
        state["transcriptions"], part
    )
    assert call["options"]["num_predict"] == nodes.PARSE_MAX_TOKENS
    [row] = step_rows(db, run)
    assert row["status"] == "ok"
    assert (row["page"], row["attempt"]) == (1, 1)
    assert row["prompt_name"] == f"parse_{state['doc_type'].value}"
    assert row["input"] == {
        "doc_type": state["doc_type"].value, "model_role": "primary",
        "attempt": 1, "pages": [p.page for p in state["transcriptions"]],
        "part": part.model_dump(mode="json"), "parts": 1,
    }  # fmt: skip
    assert row["output"]["merged"] is True
    assert out["part_provenance"][0].step_id == out["parse_provenance"].step_id


def test_parse_on_the_fallback_calls_only_the_fallback(db, run, model_client):
    state = transcribed("d09")
    state.update(doc_type=LAB, model_role="fallback", parse_attempts=0)
    model_client.replies[llm.FALLBACK_MODEL] = [reply_for("d09")]

    out = nodes.parse(state)

    assert out["parse_attempts"] == 1
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
    [answered] = row["output"]["part_results"]
    assert (answered["reply"], row["output"]["merged"]) == (reply, False)


def test_a_parse_capped_on_every_model_fails_the_step_without_raising(
    db, run, model_client
):
    state = transcribed("d09")
    state["doc_type"] = LAB
    model_client.replies[llm.PRIMARY_MODEL] = [('{"patient":', "length")]
    model_client.replies[llm.FALLBACK_MODEL] = [('{"patient":', "length")]

    out = nodes.parse(state)

    assert (out["parse"], out["parse_error"], out["parse_attempts"]) == (
        None, nodes.CAPPED, 1
    )  # fmt: skip
    [part] = out["part_results"]
    assert (part.reply, part.error) == (None, nodes.CAPPED)
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
@pytest.mark.parametrize("doc_id", MAIN_PARSED_IDS)
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


def test_validate_after_every_parse_failed_keeps_the_pages_alone(db, run):
    state = transcribed("d09")
    state.update(doc_type=LAB, parse=None, parse_error="results: Field required")

    out = nodes.validate(state)

    assert out["record_parsed"] is False
    assert out["record"].pages == state["transcriptions"]
    assert out["record"].patient is None and out["record"].observations == []
    assert out["findings"] == []


def test_a_validated_parse_is_marked_as_parsed(db, run):
    state = transcribed("d09")
    state.update(doc_type=LAB, parse=parse_of(key("d09")))
    assert nodes.validate(state)["record_parsed"] is True


# -------------------------------------------------------------------
# repair and fallback
# -------------------------------------------------------------------
def a_finding(**kw) -> Finding:
    fields = {"validator": Validator.SOURCE_TEXT, "severity": Severity.ERROR,
              "message": "value_text '99.9' is not on its cited lines"}  # fmt: skip
    return Finding(**(fields | kw))


def test_problems_are_the_error_findings_with_where_they_are():
    state = {**transcribed("d09"), "doc_type": LAB, "parse": object(), "findings": [
        a_finding(item_id="obs-1", field="value_text", page=1, lines=[16]),
        a_finding(severity=Severity.FLAG, message="flag"),
        a_finding(validator=Validator.MEDICINE_COVERAGE, item_id=None, page=1,
                  lines=[9, 10], message="this line holds a medicine"),
    ]}  # fmt: skip

    assert nodes.problems(state) == [
        "- obs-1 value_text page 1 line 16: value_text '99.9' is not on its cited lines",
        "- page 1 line 9, 10: this line holds a medicine",
    ]


def test_problems_after_a_refused_reply_are_why_it_was_refused():
    state = {"parse": None, "part_results": [
        PartResult(index=0, error="results: Field required\npatient: x")
    ]}  # fmt: skip
    assert nodes.problems(state) == ["- results: Field required", "- patient: x"]


def test_each_part_gets_the_problems_on_its_lines():
    state = {**transcribed("d01"), "doc_type": LAB, "parse": object(), "findings": [
        a_finding(item_id="obs-1", page=1, lines=[22]),
        a_finding(item_id="obs-20", page=3, lines=[20]),
        a_finding(item_id="obs-30", page=3, lines=[999]),  # past the page: its page
        a_finding(item_id=None),  # nowhere: every part
    ]}  # fmt: skip

    found = nodes.part_problems(state)

    assert sorted(found) == [0, 1, 2]
    assert [len(found[i]) for i in (0, 1, 2)] == [2, 1, 3]


def test_a_refused_part_alone_is_repaired(db, run, model_client):
    state = transcribed("d01")
    replies = part_replies("d01")
    state.update(doc_type=LAB, parse=None, parse_attempts=1, part_results=[
        PartResult(index=0, reply=replies[0], parse=LabReportParse.model_validate_json(
            replies[0])),
        PartResult(index=1, reply='{"patient": null}', error="patient: x"),
        PartResult(index=2, reply=replies[2], parse=LabReportParse.model_validate_json(
            replies[2])),
    ])  # fmt: skip
    model_client.replies[llm.PRIMARY_MODEL] = [replies[1]]

    out = nodes.repair(state)

    assert len(model_client.calls) == 1
    assert model_client.calls[0]["messages"][1]["content"].startswith("=== Page 2 ===")
    assert out["parse"] == parse_of(key("d01"))
    [row] = step_rows(db, run)
    assert row["input"]["parts"] == [1]


def repair_state(doc_id="d09") -> dict:
    state = transcribed(doc_id)
    state.update(doc_type=LAB, parse=parse_of(key(doc_id)), parse_attempts=1,
                 part_results=[PartResult(index=0, reply='{"previous": "reply"}',
                                          parse=parse_of(key(doc_id)))],
                 findings=[a_finding(item_id="obs-1", field="value_text")])  # fmt: skip
    return state


def test_repair_shows_the_model_its_reply_and_the_problems(db, run, model_client):
    state = repair_state()
    model_client.replies[llm.PRIMARY_MODEL] = [reply_for("d09")]

    out = nodes.repair(state)

    assert out["parse"] == parse_of(key("d09"))
    assert (out["parse_attempts"], out["repairs"]) == (2, 1)
    messages = model_client.calls[0]["messages"]
    [part] = nodes.parse_parts(state)
    assert messages[:2] == nodes._part_messages(state, "parse_lab_report", part)
    assert messages[2] == {"role": "assistant", "content": '{"previous": "reply"}'}
    assert messages[3]["role"] == "user"
    assert messages[3]["content"].startswith(prompt("repair_parse"))
    assert messages[3]["content"].endswith("\n".join(nodes.problems(state)))
    assert model_client.calls[0]["format"] == parse_output_schema(LAB)
    [row] = step_rows(db, run)
    assert (row["node"], row["status"]) == ("repair", "ok")
    assert row["prompt_name"] == "parse_lab_report+repair_parse"
    assert row["input"]["problems"] == 1 and row["input"]["attempt"] == 2
    assert out["parse_provenance"].node == "repair"


def test_repair_of_a_cut_off_reply_sends_no_previous_reply(db, run, model_client):
    state = repair_state()
    state.update(parse=None, part_results=[PartResult(index=0, error=nodes.CAPPED)])
    model_client.replies[llm.PRIMARY_MODEL] = [reply_for("d09")]

    nodes.repair(state)

    roles = [m["role"] for m in model_client.calls[0]["messages"]]
    assert roles == ["system", "user", "user"]


def test_a_repair_that_still_breaks_the_schema_fails_without_raising(
    db, run, model_client
):
    state = repair_state()
    model_client.replies[llm.PRIMARY_MODEL] = ['{"patient": null}']

    out = nodes.repair(state)

    assert out["parse"] is None and out["parse_attempts"] == 2
    assert out["part_results"][0].reply == '{"patient": null}'
    assert step_rows(db, run)[0]["status"] == "failed"


def test_repair_on_the_fallback_calls_only_the_fallback(db, run, model_client):
    state = repair_state()
    state["model_role"] = "fallback"
    model_client.replies[llm.FALLBACK_MODEL] = [reply_for("d09")]

    out = nodes.repair(state)

    assert [c["model"] for c in model_client.calls] == [llm.FALLBACK_MODEL]
    assert out["parse_model_role"] == "fallback"


def test_nothing_to_repair_is_a_routing_bug(db, run, model_client):
    state = repair_state()
    state["findings"] = []

    with pytest.raises(ValueError, match="nothing to repair"):
        nodes.repair(state)
    assert model_client.calls == []


def test_fallback_switches_the_model_and_starts_its_budget(db, run):
    out = nodes.use_fallback({"parse_attempts": 3, "model_role": "primary"})

    assert out == {"model_role": "fallback", "parse_attempts": 0, "part_results": []}
    [row] = step_rows(db, run)
    assert (row["node"], row["status"], row["input"]) == (
        "fallback", "ok", {"attempts": 3}
    )  # fmt: skip


# -------------------------------------------------------------------
# finish
# -------------------------------------------------------------------
def parsed_and_validated(run, model_client, doc_id: str, reply: str | None = None):
    """The state after a real parse and validate step, for this run's document."""
    state = transcribed(doc_id, expected_patient=expected_patient(doc_id))
    state.update(
        document_id=run.document_id, doc_type=DocumentType(DOCS[doc_id]["type"])
    )
    model_client.replies[llm.PRIMARY_MODEL] = [reply] if reply else part_replies(doc_id)
    while nodes.next_part(state) is not None:
        state.update(nodes.parse(state))
    state.update(nodes.validate(state))
    return state


def saved_record(db, run) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT record, run_id::text FROM document_records WHERE document_id = %s",
            (run.document_id,),
        )
        return cur.fetchone()


def test_finish_a_clean_document_is_verified_and_saved(db, run, model_client):
    state = parsed_and_validated(run, model_client, "d09")

    out = nodes.finish(state)

    assert out["end_state"] == "verified"
    assert not any(h.routes for h in out["signals"])
    record = out["record"]
    assert all(i.provenance.status is ItemStatus.VALIDATED for i in record.items())
    assert record.document.provenance.node == "parse"
    assert record.document.provenance.step_id == state["parse_provenance"].step_id
    stored, run_id = saved_record(db, run)
    assert run_id == run.id
    assert DocumentRecord.model_validate(stored) == record
    finish_row = step_rows(db, run)[-1]
    assert finish_row["node"] == "finish"
    assert finish_row["output"]["end_state"] == "verified"
    # The state's record is left as validate made it.
    assert state["record"].observations[0].provenance is None


def test_finish_raises_op3_for_the_runs_breaker_switches_without_routing(
    db, run, model_client
):
    state = parsed_and_validated(run, model_client, "d09")
    run.note_breaker_switch("model_disabled", llm.PRIMARY_MODEL, "primary")

    out = nodes.finish(state)

    assert out["end_state"] == "verified"  # operational: stored, not routed
    [hit] = [h for h in out["signals"] if h.signal == "OP-3"]
    assert hit.details == {
        "event": "model_disabled", "model": llm.PRIMARY_MODEL, "model_role": "primary"
    }  # fmt: skip
    stored = step_rows(db, run)[-1]["output"]["signals"]
    assert any(h["signal"] == "OP-3" for h in stored)


def test_finish_marks_a_wrong_patient_document_for_review(db, run, model_client):
    state = parsed_and_validated(run, model_client, "d16")

    out = nodes.finish(state)

    assert out["end_state"] == "needs_review"
    reasons = out["record"].document.provenance.low_confidence_reasons
    assert reasons == [nodes.signals.SignalHit(signal="LC-13").reason]


def test_finish_marks_an_error_left_after_every_try(db, run, model_client):
    reply = json.loads(reply_for("d09"))
    reply["results"][0]["value_text"] = "99.9"
    state = parsed_and_validated(run, model_client, "d09", json.dumps(reply))

    out = nodes.finish(state)

    assert out["end_state"] == "needs_review"
    first = out["record"].observations[0]
    assert first.provenance.status is ItemStatus.LOW_CONFIDENCE
    assert any(r.startswith("LC-04") for r in first.provenance.low_confidence_reasons)
    assert out["record"].observations[1].provenance.status is ItemStatus.VALIDATED


def test_finish_a_document_with_no_parse_is_stamped_by_the_finish_step(
    db, run, model_client
):
    state = transcribed("d09")
    state.update(document_id=run.document_id, doc_type=LAB, parse=None,
                 parse_error="results: Field required")  # fmt: skip
    state.update(nodes.validate(state))

    out = nodes.finish(state)

    assert out["end_state"] == "needs_review"
    provenance = out["record"].document.provenance
    assert provenance.node == "finish"
    assert provenance.low_confidence_reasons == [
        nodes.signals.SignalHit(signal="LC-08").reason
    ]


def test_finish_refuses_a_record_whose_items_trace_nowhere(db, run, model_client):
    state = parsed_and_validated(run, model_client, "d09")
    state["parse_provenance"] = state["parse_provenance"].model_copy(
        update={"step_id": "00000000-0000-0000-0000-000000000000"}
    )

    with pytest.raises(store.ProvenanceGapError, match="not in pipeline_steps"):
        nodes.finish(state)
    assert saved_record(db, run) is None
    assert step_rows(db, run)[-1]["status"] == "failed"


def test_finish_without_a_record_is_a_routing_bug(db, run):
    with pytest.raises(ValueError, match="no record"):
        nodes.finish({"document_id": run.document_id})


# -------------------------------------------------------------------
# render
# -------------------------------------------------------------------
def test_render_builds_the_view_from_the_record_without_a_model(db, run, model_client):
    state = parsed_and_validated(run, model_client, "d09")
    calls = len(model_client.calls)

    html = nodes.render(state)["insight_html"]

    assert len(model_client.calls) == calls
    for o in state["record"].observations:
        assert f'data-item="{o.id}"' in html
        assert o.printed.test_name in html
    [*_, row] = step_rows(db, run)
    assert (row["node"], row["prompt_name"]) == ("render", None)
    assert row["input"] == {
        "pages": [1], "record_parsed": True, "findings": len(state["findings"])
    }  # fmt: skip


def test_render_shows_the_review_marks_finish_saves(db, run, model_client):
    # A flag finding on the first result: render marks it as finish does.
    state = parsed_and_validated(run, model_client, "d09")
    state["findings"] = [
        *state["findings"],
        Finding(validator=Validator.FLAG_AGREEMENT, severity=Severity.FLAG,
                message="flag differs", item_id="obs-1", field="flag_printed"),
    ]  # fmt: skip

    html = nodes.render(state)["insight_html"]
    out = nodes.finish(state)

    assert html == document_html(out["record"])
    assert "Some values in this document await review" in html
    assert "The printed flag differs from the flag computed from the range." in html


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
