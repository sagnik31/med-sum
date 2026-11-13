"""
The read-only tool module: specs shown to the model, calls checked against
each tool's schema, get_lines' answers and citations, and what may be logged.
"""

import json

import pytest
from dataset_records import keyed_record

from record.document import PageTranscription
from tools import GET_LINES, TOOLS, ToolSource, invoke, specs, subset
from tools import transcription as tools_transcription
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
    assert sent["cite"] == "p1:6"
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


# -------------------------------------------------------------------
# search_transcription
# -------------------------------------------------------------------
def test_search_finds_every_line_ignoring_case_and_spacing():
    out = call("search_transcription", {"text": "serum   SODIUM"}).output
    assert [(h.page, h.line, h.cite) for h in out.hits] == [(1, 6, "p1:6")]
    assert out.hits[0].text == "| Serum Sodium | 139 | mmol/L |"
    assert out.more is False


def test_search_on_one_page_only():
    out = call("search_transcription", {"text": "line 1", "page": 1}).output
    assert out.hits == []
    hits = call("search_transcription", {"text": "line 1", "page": 2}).output.hits
    assert all(h.page == 2 for h in hits) and hits


def test_a_broad_search_is_cut_and_says_so():
    out = call("search_transcription", {"text": "line"}).output
    assert len(out.hits) == tools_transcription.MAX_HITS and out.more is True


@pytest.mark.parametrize("text", ["x", " ", "y" * 121])
def test_a_search_too_short_or_long_is_malformed(text):
    result = call("search_transcription", {"text": text})
    assert result.error.kind == "malformed_arguments"


def test_a_search_on_a_missing_page_is_not_found():
    assert call("search_transcription", {"text": "Sodium", "page": 9}).error.kind == (
        "not_found"
    )


# -------------------------------------------------------------------
# get_text_layer
# -------------------------------------------------------------------
LAYERED = ToolSource(
    pages=(
        PageTranscription(page=1, lines=["a"], text_layer="Serum Sodium 139\nmmol/L"),
        PageTranscription(page=2, lines=["b"]),
    )
)


def test_the_text_layer_comes_back_as_numbered_lines():
    out = invoke(TOOLS, "get_text_layer", {"page": 1}, LAYERED).output
    assert out.has_text_layer and out.layer_lines == 2 and out.cut is False
    assert [(line.number, line.text) for line in out.lines] == [
        (1, "Serum Sodium 139"),
        (2, "mmol/L"),
    ]


def test_a_page_without_a_text_layer_says_so():
    out = invoke(TOOLS, "get_text_layer", {"page": 2}, LAYERED).output
    assert out.has_text_layer is False and out.lines == []


def test_the_text_layer_is_cut_at_the_limit():
    long = ToolSource(
        pages=(PageTranscription(page=1, lines=[], text_layer="x\n" * 100),)
    )
    out = invoke(TOOLS, "get_text_layer", {"page": 1, "first": 5}, long).output
    assert [line.number for line in out.lines][0] == 5
    assert len(out.lines) == MAX_LINES and out.cut is True


def test_text_layer_errors():
    assert invoke(TOOLS, "get_text_layer", {"page": 3}, LAYERED).error.kind == (
        "not_found"
    )
    bad = invoke(TOOLS, "get_text_layer", {"page": 1, "first": 2, "last": 1}, LAYERED)
    assert bad.error.kind == "malformed_arguments"


# -------------------------------------------------------------------
# list_items and get_item (over an answer key's record)
# -------------------------------------------------------------------
@pytest.fixture
def d09():
    record = keyed_record("d09")
    return ToolSource(pages=tuple(record.pages), record=record)


def test_list_items_gives_each_item_with_its_kind_name_and_citation(d09):
    out = invoke(TOOLS, "list_items", {}, d09).output
    assert [i.item_id for i in out.items] == [i.id for i in d09.record.items()]
    first = out.items[0]
    c = d09.record.observations[0].printed.citation
    assert first.kind == "lab_result"
    assert first.name == d09.record.observations[0].printed.test_name
    assert first.cite == f"p{c.page}:{c.first_line}"


def test_list_items_of_one_kind(d09):
    out = invoke(TOOLS, "list_items", {"kind": "remark"}, d09).output
    assert {i.kind for i in out.items} == {"remark"}
    assert invoke(TOOLS, "list_items", {"kind": "x"}, d09).error.kind == (
        "malformed_arguments"
    )


def test_get_item_gives_the_item_and_the_text_it_cites(d09):
    out = invoke(TOOLS, "get_item", {"item_id": "obs-1"}, d09).output
    item = d09.record.observations[0]
    c = item.printed.citation
    assert out.printed["test_name"] == item.printed.test_name
    assert (
        out.cited_lines == d09.pages[c.page - 1].lines[c.first_line - 1 : c.last_line]
    )
    assert out.cite == f"p{c.page}:{c.first_line}"
    assert out.kind == "lab_result"


def test_get_item_of_an_unknown_id_is_not_found(d09):
    assert invoke(TOOLS, "get_item", {"item_id": "obs-99"}, d09).error.kind == (
        "not_found"
    )


def test_record_tools_without_a_record_say_there_is_none():
    for name, args in (("list_items", {}), ("get_item", {"item_id": "obs-1"})):
        result = invoke(TOOLS, name, args, SOURCE)
        assert result.error.kind == "not_found"
        assert "no record" in result.error.message


def test_every_tool_spec_is_an_object_schema_without_titles():
    for spec in specs(TOOLS):
        params = spec["function"]["parameters"]
        assert params["type"] == "object" and params["additionalProperties"] is False
        assert "title" not in params


def test_subset_keeps_the_order_and_refuses_unknown_names():
    assert list(subset("get_item", "get_lines")) == ["get_lines", "get_item"]
    with pytest.raises(KeyError):
        subset("delete_page")
