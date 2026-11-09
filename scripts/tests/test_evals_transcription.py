"""The transcription scorer, tested on the dataset's expected transcriptions."""

import json

import pytest

from dataset_records import DOC_IDS, key
from evals.transcription import (
    content_lines,
    edit_distance,
    page_edits,
    score_transcription,
    shaped_lines,
    summarise_transcriptions,
)


def pages(doc_id):
    """A fresh copy of the expected transcription, safe to change."""
    return [p.model_copy(deep=True) for p in key(doc_id).pages]


def replace_line(page, old, new):
    page.lines[page.lines.index(old)] = new


HB_ROW = "| Haemoglobin | 10.4 | L | g/dL | 12.0 - 15.0 |"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "a, b, d",
    [("", "", 0), ("abc", "", 3), ("", "ab", 2), ("kitten", "sitting", 3),
     ("10.4", "10.9", 1), ("same", "same", 0)],
)  # fmt: skip
def test_edit_distance(a, b, d):
    assert edit_distance(a, b) == d == edit_distance(b, a)


def test_markdown_furniture_is_not_content():
    assert content_lines(
        ["# Sunrise Diagnostics", "", "| Test | Result |", "| --- | --- |",
         "|  Hb | 10.4 |  |", "<!-- page 1 -->", "  ##  CBC  "]
    ) == ["Sunrise Diagnostics", "Test Result", "Hb 10.4", "CBC"]  # fmt: skip


def test_page_edits_align_lines_before_counting_characters():
    expected = ["a" * 30, "Hb 10.4", "b" * 30]
    assert page_edits(expected, expected) == 0
    assert page_edits(expected, ["a" * 30, "Hb 10.9", "b" * 30]) == 1
    assert page_edits(expected, ["a" * 30, "b" * 30]) == len("Hb 10.4\n") - 1


# -------------------------------------------------------------------
# Scores
# -------------------------------------------------------------------
@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_the_expected_transcription_scores_perfectly(doc_id):
    s = score_transcription(pages(doc_id), key(doc_id))
    assert s.cer == 0 and s.char_edits == 0
    assert s.lines.fp == s.lines.fn == 0 and s.lines.tp > 0
    assert s.values.correct == s.values.total and s.missing_values == []
    assert s.pages_got == s.pages_expected and s.unreadable == 0


def test_table_formatting_does_not_count():
    got = pages("d01")
    replace_line(got[0], HB_ROW, "|Haemoglobin|10.4|L|g/dL|12.0 - 15.0|")
    got[0].lines = [line for line in got[0].lines if not line.startswith("| ---")]
    s = score_transcription(got, key("d01"))
    assert s.char_edits == 0 and s.lines.fn == 0


def test_a_misread_value_costs_characters_a_line_and_the_value():
    got = pages("d01")
    replace_line(got[0], HB_ROW, HB_ROW.replace("10.4", "10.9"))
    s = score_transcription(got, key("d01"))
    assert s.char_edits == 1
    assert (s.lines.fp, s.lines.fn) == (1, 1)
    assert [(m.item_id, m.field, m.text) for m in s.missing_values] == [
        ("obs-1", "value_text", "10.4")
    ]


def test_case_is_kept():
    got = pages("d01")
    replace_line(got[0], HB_ROW, HB_ROW.replace("g/dL", "G/DL"))
    s = score_transcription(got, key("d01"))
    assert s.char_edits == 2
    assert ("unit", "g/dL") in {(m.field, m.text) for m in s.missing_values}


def test_a_dropped_line_loses_its_values():
    got = pages("d01")
    got[0].lines.remove(HB_ROW)
    s = score_transcription(got, key("d01"))
    assert (s.lines.fp, s.lines.fn) == (0, 1)
    assert {m.item_id for m in s.missing_values} == {"obs-1"}


def test_a_value_on_the_wrong_page_is_missing():
    got = pages("d01")
    got[0].lines.remove(HB_ROW)
    got[1].lines.append(HB_ROW)
    s = score_transcription(got, key("d01"))
    assert {m.item_id for m in s.missing_values} == {"obs-1"}


def test_a_missing_page_loses_everything_on_it():
    got = pages("d01")[:-1]
    s = score_transcription(got, key("d01"))
    assert (s.pages_expected, s.pages_got) == (3, 2)
    assert s.lines.fn == len(content_lines(key("d01").pages[2].lines))
    assert s.missing_values and {m.page for m in s.missing_values} == {3}


def test_an_extra_page_adds_false_positives():
    got = pages("d11")
    extra = got[0].model_copy(update={"page": 2})
    s = score_transcription(got + [extra], key("d11"))
    assert s.pages_got == 2 and s.lines.fp == s.lines.tp and s.lines.fn == 0


def test_unreadable_lines_are_counted():
    got = pages("d01")
    replace_line(
        got[0], HB_ROW, "| Haemoglobin | [unreadable] | L | g/dL | 12.0 - 15.0 |"
    )
    s = score_transcription(got, key("d01"))
    assert s.unreadable == 1
    assert ("value_text", "10.4") in {(m.field, m.text) for m in s.missing_values}


def test_a_failed_transcription_misses_everything():
    s = score_transcription(None, key("d02"))
    assert s.pages_got == 0 and s.lines.tp == 0 and s.lines.fn > 0
    assert s.cer == 1.0 and s.values.correct == 0 and s.values.total > 0


def test_summary_adds_documents_up_and_serialises():
    a = score_transcription(pages("d01"), key("d01"))
    b = score_transcription(None, key("d02"))
    total = summarise_transcriptions([a, b])
    assert total.documents == 2
    assert total.lines == a.lines + b.lines
    assert total.cer == pytest.approx(
        b.chars_expected / (a.chars_expected + b.chars_expected)
    )
    assert json.loads(json.dumps(total.as_dict()))["documents"] == 2
    assert json.loads(json.dumps(b.as_dict()))["missing_values"]


def test_values_are_found_wherever_the_model_numbered_their_lines():
    got = pages("d01")
    for p in got:
        p.lines[:0] = ["Scanned copy", ""]  # every line moves down by two
    s = score_transcription(got, key("d01"))
    assert s.values.correct == s.values.total
    assert (s.lines.fp, s.lines.fn) == (3, 0)


# -------------------------------------------------------------------
# Layout (reported apart from content)
# -------------------------------------------------------------------
def test_line_shapes():
    assert shaped_lines(["# Title", "## CBC", "| a | b |  |", "Plain text"]) == [
        ("Title", "heading 1"),
        ("CBC", "heading 2"),
        ("a b", "table 3 cells"),
        ("Plain text", "text"),
    ]


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_the_expected_transcription_keeps_its_layout(doc_id):
    s = score_transcription(pages(doc_id), key(doc_id))
    assert s.layout.correct == s.layout.total > 0 and s.layout_differences == []


def test_a_table_row_written_as_text_is_a_layout_difference_only():
    got = pages("d01")
    replace_line(got[0], HB_ROW, "Haemoglobin 10.4 L g/dL 12.0 - 15.0")
    s = score_transcription(got, key("d01"))
    assert s.char_edits == 0 and s.lines.fn == 0  # content is right
    assert [(d.page, d.text, d.expected, d.got) for d in s.layout_differences] == [
        (1, "Haemoglobin 10.4 L g/dL 12.0 - 15.0", "table 5 cells", "text")
    ]
    assert s.layout.correct == s.layout.total - 1


def test_a_lost_heading_or_merged_cells_are_layout_differences():
    got = pages("d01")
    replace_line(got[0], "## COMPLETE BLOOD COUNT", "COMPLETE BLOOD COUNT")
    replace_line(got[0], HB_ROW, "| Haemoglobin 10.4 | L | g/dL | 12.0 - 15.0 |")
    s = score_transcription(got, key("d01"))
    assert {(d.expected, d.got) for d in s.layout_differences} == {
        ("heading 2", "text"),
        ("table 5 cells", "table 4 cells"),
    }


def test_layout_is_only_judged_on_lines_whose_content_is_right():
    got = pages("d01")
    replace_line(got[0], HB_ROW, "Haemoglobin 10.9 L g/dL 12.0 - 15.0")
    s = score_transcription(got, key("d01"))
    assert s.layout_differences == []
    assert s.layout.total == s.lines.tp


def test_layout_adds_up_in_the_summary():
    got = pages("d01")
    replace_line(got[0], HB_ROW, "Haemoglobin 10.4 L g/dL 12.0 - 15.0")
    a = score_transcription(got, key("d01"))
    total = summarise_transcriptions([a, score_transcription(pages("d02"), key("d02"))])
    assert total.layout.correct == total.layout.total - 1
    assert json.loads(json.dumps(total.as_dict()))["layout"]["total"] > 0
