"""
The tool-calling probe, scored on scripted replies from the fake client.
"""

import json

from conftest import ToolReply

import llm
from evals import toolprobe

PERFECT = {
    "explicit_lines": [ToolReply(("get_lines", {"page": 1, "first": 8, "last": 10}))],
    "finding_cites_a_line": [
        ToolReply(("get_lines", {"page": 1, "first": 7, "last": 11}))
    ],
    "finding_on_page_two": [
        ToolReply(("get_lines", {"page": 2, "first": 1, "last": 5}))
    ],
    "find_the_right_line": [
        ToolReply(("get_lines", {"page": 1, "first": 11, "last": 16})),
        "15",
    ],
    "no_tool_needed": ["No"],
}


def script(model_client, model, replies):
    model_client.replies[model] = [
        r for case in toolprobe.CASES for r in replies[case.name]
    ]


def test_every_case_passes_on_a_model_that_calls_the_tools_right(
    model_client, tmp_path
):
    script(model_client, llm.PRIMARY_MODEL, PERFECT)
    assert (
        toolprobe.main(["--live", "--model", llm.PRIMARY_MODEL, "--out", str(tmp_path)])
        == 0
    )
    report = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    s = report["summary"][llm.PRIMARY_MODEL]
    assert s["passed"] == s["cases"] == len(toolprobe.CASES)
    assert s["on_target"] == s["tool_cases"] == 4


def test_the_tool_answer_goes_back_as_a_tool_message(model_client):
    case = next(c for c in toolprobe.CASES if c.name == "find_the_right_line")
    model_client.replies[llm.PRIMARY_MODEL] = list(PERFECT[case.name])
    result = toolprobe.run_case(case, "primary")
    assert result.passed and result.answered
    second = model_client.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]
    tool_msg = second[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_name"] == "get_lines"
    assert json.loads(tool_msg["content"])["cite"] == "p1:11-16"


def test_a_model_that_answers_without_calling_fails_a_tool_case(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ["Lines 8-10 are the renal rows."]
    result = toolprobe.run_case(toolprobe.CASES[0], "primary")
    assert not result.called and not result.passed


def test_a_malformed_call_is_not_well_formed(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": 1, "lines": "8-10"}))
    ]
    result = toolprobe.run_case(toolprobe.CASES[0], "primary")
    assert result.called and result.right_tool
    assert not result.well_formed and not result.on_target and not result.passed


def test_string_numbers_are_on_target_but_not_exact_types(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": "1", "first": "8", "last": "10"}))
    ]
    result = toolprobe.run_case(toolprobe.CASES[0], "primary")
    assert result.passed and result.well_formed and not result.exact_types


def test_reading_far_more_than_needed_is_off_target(model_client):
    case = next(c for c in toolprobe.CASES if c.name == "finding_cites_a_line")
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": 1, "first": 1, "last": 40}))
    ]
    assert not toolprobe.run_case(case, "primary").on_target


def test_calling_a_tool_when_none_is_needed_fails(model_client):
    case = next(c for c in toolprobe.CASES if c.name == "no_tool_needed")
    model_client.replies[llm.PRIMARY_MODEL] = [
        ToolReply(("get_lines", {"page": 2, "first": 8, "last": 8})),
        "No",
    ]
    result = toolprobe.run_case(case, "primary")
    assert result.called and result.answered and not result.passed


def test_a_model_error_ends_the_case(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("down")]
    result = toolprobe.run_case(toolprobe.CASES[0], "primary")
    assert result.error == "ModelOutputError" and not result.passed


def test_no_probe_line_with_a_value_is_a_dataset_transcription_line():
    """Headings and table header rows may be shared; names and values not."""
    from dataset_records import DOCS, key

    def valued(line):
        return any(ch.isdigit() for ch in line) or ":" in line

    probe_lines = {line for p in toolprobe.PAGES for line in p.lines if valued(line)}
    for doc in DOCS:
        for page in key(doc).pages:
            assert not probe_lines & {line for line in page.lines if line.strip()}
