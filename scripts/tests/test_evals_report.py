"""The eval report: data, Markdown, files, and the harness self-check."""

import json
import re
from datetime import datetime, timezone

import pytest

from dataset_records import DOC_IDS, MANIFEST, key
from evals import selfcheck
from evals.report import (
    REPORT_VERSION,
    DocumentResult,
    ModelRun,
    build_report,
    render_markdown,
    write_report,
)
from evals.score import score_document
from evals.transcription import score_transcription

AT = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def perfect_run(model="qwen", seconds=None):
    return ModelRun(
        model,
        [
            DocumentResult(d, score_document(key(d), key(d)),
                           score_transcription(key(d).pages, key(d)), seconds)
            for d in DOC_IDS
        ],
    )  # fmt: skip


def flawed_run(model="gemma"):
    """
    d01 with a misread haemoglobin and an invented test, d25 with a misread
    haemoglobin, and d02 not run at all.
    """
    run = perfect_run(model)
    pred = key("d01")
    hb = next(o for o in pred.observations if o.printed.test_name == "Haemoglobin")
    hb.printed.value_text = "10.9"
    extra = hb.model_copy(deep=True)
    extra.id, extra.printed.test_name = "obs-invented", "Serum Ferritin"
    pred.observations.append(extra)
    run.documents[0] = DocumentResult(
        "d01", score_document(pred, key("d01")), run.documents[0].transcription
    )
    run.documents[1] = DocumentResult("d02", score_document(None, key("d02")))
    long = key("d25")
    hb = next(o for o in long.observations if o.printed.test_name == "Haemoglobin")
    hb.printed.value_text = "13.2"
    at = DOC_IDS.index("d25")
    run.documents[at] = DocumentResult(
        "d25", score_document(long, key("d25")), run.documents[at].transcription
    )
    return run


def report(*runs):
    return build_report("test", list(runs), MANIFEST, generated_at=AT)


# -------------------------------------------------------------------
# Data
# -------------------------------------------------------------------
def test_the_report_is_json_and_names_its_run():
    r = json.loads(json.dumps(report(perfect_run())))
    assert r["label"] == "test" and r["dataset"] == MANIFEST["name"]
    assert r["generated_at"] == "2026-11-09T12:00:00+00:00"
    assert r["report_version"] == REPORT_VERSION
    assert list(r["models"]) == ["qwen"]


def test_the_model_choice_is_in_the_report():
    r = report(perfect_run("qwen"), flawed_run("gemma"))
    assert r["choice"]["model"] == "qwen"
    # gemma: 3 unsafe errors over the dataset's 204 clinical items, above
    # the 1% gate (2 would not be: the gate is a rate).
    assert set(r["choice"]["excluded"]) == {"gemma"}


def test_cases_list_their_documents_and_score_them():
    r = report(flawed_run())
    expected = {}
    for doc in MANIFEST["documents"]:
        for case in doc["cases"]:
            expected.setdefault(case, []).append(doc["id"])
    assert r["cases"] == {c: sorted(ids) for c, ids in sorted(expected.items())}
    cases = r["models"]["gemma"]["cases"]
    assert cases["single_creatinine"]["field_f1"] == 1.0
    d01_case = next(c for c in MANIFEST["documents"][0]["cases"])
    assert cases[d01_case]["unsafe"] == 2  # the misread and the invented test
    assert cases[d01_case]["hallucinated"] == 1


def test_documents_are_in_id_order_with_their_scores():
    run = perfect_run(seconds=120.0)
    run.documents.reverse()
    docs = report(run)["models"]["qwen"]["documents"]
    assert [d["record"]["document_id"] for d in docs] == DOC_IDS
    assert docs[0]["transcription"]["cer"] == 0 and docs[0]["seconds"] == 120.0
    assert report(run)["models"]["qwen"]["seconds_per_document"] == 120.0


def test_a_run_without_transcriptions_reports_none():
    run = perfect_run()
    for d in run.documents:
        d.transcription = None
    r = report(run)
    assert r["models"]["qwen"]["transcription"] is None
    assert r["choice"]["model"] is None
    assert r["choice"]["excluded"]["qwen"] == "no transcription scores"


def test_no_runs():
    r = report()
    assert r["choice"] is None and r["models"] == {}
    assert "No models were run." in render_markdown(r)


# -------------------------------------------------------------------
# Markdown
# -------------------------------------------------------------------
def test_markdown_has_every_section_and_column():
    md = render_markdown(report(perfect_run("qwen"), flawed_run("gemma")))
    for heading in ("# Eval results: test", "## Model choice", "## Overview",
                    "## By case", "## By document", "## Errors",
                    "### `qwen`", "### `gemma`"):  # fmt: skip
        assert heading in md, heading
    assert "| Metric | qwen | gemma |" in md
    assert "**Primary: `qwen`**" in md
    assert "- `gemma` excluded: unsafe-error rate" in md


def test_markdown_lists_unsafe_missed_and_invented_items():
    md = render_markdown(report(flawed_run()))
    assert (
        "- d01 unsafe: observations obs-1 value_text expected `10.4`, got `10.9`" in md
    )
    assert "- d01 unsafe: invented observations item `Serum Ferritin`" in md
    assert "- d02 missed medications med-1 `Glycomet`" in md
    assert "| d02 | (no record) | **no** |" in md
    # An invented clinical item is listed once, as unsafe, not again as invented.
    assert "d01 invented observations" not in md


def test_markdown_for_a_perfect_run_says_there_are_no_errors():
    md = render_markdown(report(perfect_run()))
    assert "No unsafe errors, missed items or invented items." in md
    assert "| Field F1 | 100.0% |" in md
    assert "| Median time per document | - |" in md


def test_every_table_row_has_as_many_cells_as_its_header():
    run = flawed_run()
    # A pipe inside a value must be escaped, or it would split its cell.
    run.documents[2].record.document_id = "d|03"
    md = render_markdown(report(perfect_run(), run))
    assert "d\\|03" in md
    width = None
    for line in md.splitlines():
        if not line.startswith("|"):
            width = None
            continue
        cells = len(re.split(r"(?<!\\)\|", line)) - 2
        if width is None:
            width = cells
        assert cells == width, line


# -------------------------------------------------------------------
# Files and the self-check
# -------------------------------------------------------------------
def test_write_report(tmp_path):
    r = report(perfect_run())
    json_path, md_path = write_report(r, tmp_path / "run")
    assert json.loads(json_path.read_text(encoding="utf-8")) == r
    assert md_path.read_text(encoding="utf-8") == render_markdown(r)


def test_the_selfcheck_scores_everything_perfectly(tmp_path, capsys):
    selfcheck.main([str(tmp_path)])
    r = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    s = r["models"][selfcheck.LABEL]["summary"]
    t = r["models"][selfcheck.LABEL]["transcription"]
    assert s["documents"] == len(DOC_IDS)
    assert s["all_fields"]["fn"] == s["all_fields"]["fp"] == 0
    assert s["unsafe"] == s["hallucinated"] == s["missed"] == 0
    assert t["char_edits"] == 0 and t["values"]["value"] == 1.0
    assert r["choice"]["model"] == selfcheck.LABEL
    assert "wrote" in capsys.readouterr().out


@pytest.mark.parametrize("doc_id", ["d01", "d17"])
def test_answer_keys_load_from_the_dataset(doc_id):
    from evals.keys import answer_key

    assert answer_key(doc_id) == key(doc_id)
