"""
The read-only tool module: specs shown to the model, calls checked against
each tool's schema, get_lines' answers and citations, and what may be logged.
"""

import json

import pytest

from record.document import PageTranscription
from tools import GET_LINES, TOOLS, ToolSource, invoke, specs
from tools.transcription import MAX_LINES

# An invented page; no line is taken from the dataset.
PAGE_1 = PageTranscription(
    page=1,
    lines=[
        "# Lakeview Test Centre",
        "Name: Ms. Example Patient",
        "",
        "| Test | Result | Unit |",
        "|---|---|---|",
        "| Serum Sodium | 139 | mmol/L |",
        "| Serum Potassium | 4.4 | mmol/L |",
    ],
)
PAGE_2 = PageTranscription(page=2, lines=[f"line {n}" for n in range(1, 61)])
SOURCE = ToolSource(pages=(PAGE_1, PAGE_2))


def call(name, arguments):
    return invoke(TOOLS, name, arguments, SOURCE)


# -------------------------------------------------------------------
# What the model is shown
# -------------------------------------------------------------------
def test_the_spec_is_an_ollama_function_with_the_input_schema():
    spec = GET_LINES.spec()
    assert spec["type"] == "function"
    fn = spec["function"]
    assert fn["name"] == "get_lines"
    assert fn["description"]
    params = fn["parameters"]
    assert params["type"] == "object"
    assert set(params["required"]) == {"page", "first", "last"}
    assert {p["type"] for p in params["properties"].values()} == {"integer"}
    assert params["additionalProperties"] is False
    assert "title" not in params
    assert all("title" not in p for p in params["properties"].values())


def test_specs_lists_every_tool():
    assert [s["function"]["name"] for s in specs(TOOLS)] == list(TOOLS)


def test_every_tool_is_registered_by_its_own_name():
    assert all(name == tool.name for name, tool in TOOLS.items())


# -------------------------------------------------------------------
# get_lines
# -------------------------------------------------------------------
def test_get_lines_returns_the_lines_with_their_numbers_and_a_citation():
    result = call("get_lines", {"page": 1, "first": 6, "last": 7})
    assert result.ok and result.exact_types
    out = result.output
    assert [(line.number, line.text) for line in out.lines] == [
        (6, "| Serum Sodium | 139 | mmol/L |"),
        (7, "| Serum Potassium | 4.4 | mmol/L |"),
    ]
    assert (out.page, out.first, out.last, out.page_lines) == (1, 6, 7, 7)
    assert out.cut is False
    assert out.cite == "p1:6-7"


def test_a_blank_line_is_returned_as_it_is():
    out = call("get_lines", {"page": 1, "first": 3, "last": 3}).output
    assert [(line.number, line.text) for line in out.lines] == [(3, "")]


def test_a_range_past_the_page_end_is_cut_there_and_says_so():
    out = call("get_lines", {"page": 1, "first": 5, "last": 30}).output
    assert [line.number for line in out.lines] == [5, 6, 7]
    assert out.cut is True
    assert out.cite == "p1:5-7"


def test_at_most_the_limit_of_lines_comes_back():
    out = call("get_lines", {"page": 2, "first": 1, "last": 60}).output
    assert len(out.lines) == MAX_LINES
    assert out.last == MAX_LINES and out.cut is True


def test_numbers_sent_as_strings_are_read_but_marked_inexact():
    result = call("get_lines", {"page": "1", "first": "6", "last": "6"})
    assert result.ok
    assert result.exact_types is False
    assert result.output.lines[0].number == 6


def test_arguments_sent_as_json_text_are_read():
    result = call("get_lines", json.dumps({"page": 1, "first": 1, "last": 1}))
    assert result.ok
    assert result.output.lines[0].text == "# Lakeview Test Centre"


@pytest.mark.parametrize(
    "page, first, why",
    [(3, 1, "no page 3; pages: 1, 2"), (1, 8, "page 1 has 7 lines")],
)
def test_a_page_or_line_that_is_not_there_is_not_found(page, first, why):
    result = call("get_lines", {"page": page, "first": first, "last": first})
    assert not result.ok
    assert result.error.kind == "not_found"
    assert why in result.error.message


# -------------------------------------------------------------------
# Malformed calls never raise
# -------------------------------------------------------------------
@pytest.mark.parametrize(
    "arguments, where",
    [
        ({"page": 1, "first": 1}, "last"),
        ({"page": 0, "first": 1, "last": 1}, "page"),
        ({"page": 1, "first": 5, "last": 2}, "last must not be before first"),
        ({"page": 1, "first": 1, "last": 1, "lines": 3}, "lines"),
        ({"page": "one", "first": 1, "last": 1}, "page"),
        ([1, 1, 1], "not a JSON object"),
        ("not json", "not a JSON object"),
        (None, "not a JSON object"),
    ],
)
def test_a_malformed_call_is_an_error_result(arguments, where):
    result = call("get_lines", arguments)
    assert not result.ok
    assert result.error.kind == "malformed_arguments"
    assert where in result.error.message


def test_a_malformed_call_does_not_echo_the_values_sent():
    result = call("get_lines", {"page": "Ms. Example Patient", "first": 1, "last": 1})
    assert "Example" not in result.error.message
    assert "Example" not in result.for_model()


def test_an_unknown_tool_is_an_error_naming_the_tools():
    result = call("delete_page", {"page": 1})
    assert result.error.kind == "unknown_tool"
    assert "get_lines" in result.error.message


# -------------------------------------------------------------------
# What goes back to the model, and what may be logged
# -------------------------------------------------------------------
def test_the_model_gets_the_output_as_json():
    sent = json.loads(call("get_lines", {"page": 1, "first": 6, "last": 6}).for_model())
    assert sent["cite"] == "p1:6-6"
    assert sent["lines"] == [{"number": 6, "text": "| Serum Sodium | 139 | mmol/L |"}]


def test_the_model_gets_an_error_as_json():
    sent = json.loads(call("get_lines", {"page": 9, "first": 1, "last": 1}).for_model())
    assert sent["error"] == "not_found"


def test_log_fields_hold_no_page_content():
    result = call("get_lines", {"page": 1, "first": 1, "last": 7})
    fields = result.log_fields()
    assert fields == {"tool": "get_lines", "ok": True, "lines_returned": 7}
    text = json.dumps(fields)
    assert not any(line and line in text for line in PAGE_1.lines)


def test_log_fields_of_an_error_name_its_kind():
    fields = call("get_lines", {"page": 1}).log_fields()
    assert fields == {
        "tool": "get_lines",
        "ok": False,
        "error_kind": "malformed_arguments",
    }
