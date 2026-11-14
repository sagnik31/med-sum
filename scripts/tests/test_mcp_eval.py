"""
The MCP client eval's questions, scoring and one exchange, with a scripted
model over the real server and a fake API. The eval itself is not run: it
needs a live model and a loaded database.
"""

import json

import anyio
import httpx
import pytest
from mcp import Client

import llm
from evals import mcp_eval
from evals.mcp_eval import Question, Session, score
from medsum_mcp import server as mcp_server
from medsum_mcp.api import ApiClient, Credentials
from test_mcp_server import DOC, EMAIL, PASSWORD, PATIENT, FakeApi
from test_review import flagged_record

HB = Question(
    id="d99-obs-1",
    kind="value",
    text="What was Asha Rao's Haemoglobin result in cbc.pdf?",
    patient="Asha Rao",
    file="cbc.pdf",
    expected="10.4",
    page=1,
    lines=(8, 8),
)
ABSENT = Question(
    id="asha-absent",
    kind="absent",
    text="What was Asha Rao's HbA1c result?",
    patient="Asha Rao",
)
LINE = f"document:{DOC}#p1:8"


def session(*cites, text=("| Haemoglobin | 10.4 L | g/dL | 12.0 - 15.0 |",)):
    s = Session(names={DOC: "cbc.pdf"})
    for c in cites:
        s.add({"citations": [c], "result": {}}, "")
    s.text.extend(text)
    return s


# -------------------------------------------------------------------
# Questions
# -------------------------------------------------------------------
def test_questions_come_from_the_keys_in_a_fixed_order():
    first, again = mcp_eval.build_questions(), mcp_eval.build_questions()
    assert first == again
    kinds = {q.kind for q in first}
    assert kinds == {"value", "absent"}
    for q in first:
        if q.kind == "value":
            assert q.expected and q.file and q.page and q.lines[0] <= q.lines[1]
            assert q.patient in q.text and q.file in q.text


def test_an_absent_question_asks_for_a_test_none_of_the_patients_documents_holds():
    from evals import keys

    absent = [q for q in mcp_eval.build_questions(None) if q.kind == "absent"]
    assert absent
    for q in absent:
        patient = next(p for p in keys.manifest()["patients"] if p["name"] == q.patient)
        held = {
            mcp_eval._norm(o.printed.test_name)
            for doc_id, entry in keys.documents().items()
            if entry["patient"] == patient["key"]
            for o in keys.answer_key(doc_id).observations
        }
        asked = q.text.removeprefix(f"What was {q.patient}'s ").removesuffix(" result?")
        assert mcp_eval._norm(asked) not in held


def test_the_split_narrows_the_value_questions_only():
    every, tests = mcp_eval.build_questions(None), mcp_eval.build_questions("test")
    assert len([q for q in tests if q.kind == "value"]) < len(
        [q for q in every if q.kind == "value"]
    )
    assert [q for q in tests if q.kind == "absent"] == [
        q for q in every if q.kind == "absent"
    ]


# -------------------------------------------------------------------
# Scoring
# -------------------------------------------------------------------
def test_a_cited_grounded_correct_answer_passes():
    s = score(HB, f"Haemoglobin was 10.4 g/dL ({LINE}).", session(LINE))
    assert (s.cited, s.grounded, s.numbers, s.located, s.correct) == (True,) * 5
    assert s.passed("value")


def test_a_citation_no_tool_returned_is_not_grounded():
    s = score(HB, f"Haemoglobin was 10.4 ({LINE}).", session(f"document:{DOC}#p1:9"))
    assert s.cited and not s.grounded and not s.passed("value")


def test_a_citation_inside_a_returned_range_is_grounded():
    s = score(HB, f"10.4 ({LINE})", session(f"document:{DOC}#p1:5-12"))
    assert s.grounded and s.located


def test_a_number_the_tools_never_returned_fails_the_number_check():
    s = score(HB, f"Haemoglobin was 10.4, up from 9.1 ({LINE}).", session(LINE))
    assert not s.numbers and s.unknown_numbers == ["9.1"]


def test_an_uncited_answer_fails_even_when_right():
    s = score(HB, "Haemoglobin was 10.4 g/dL.", session(LINE))
    assert s.correct and not s.cited and not s.passed("value")


def test_the_wrong_document_is_not_located():
    s = score(
        HB,
        f"10.4 ({LINE})",
        Session(cites=session(LINE).cites, text=["10.4"], names={DOC: "other.pdf"}),
    )
    assert s.grounded and not s.located


def test_abstaining_passes_an_absent_question_and_fails_a_value_one():
    s = score(ABSENT, "The records hold no answer.", session())
    assert s.abstained and s.passed("absent")
    s = score(HB, "The records hold no answer.", session(LINE))
    assert not s.passed("value")


def test_an_abstention_with_a_citation_or_a_number_is_not_one():
    assert not score(
        ABSENT, f"The records hold no answer ({LINE}).", session(LINE)
    ).abstained
    assert not score(
        ABSENT, "The records hold no answer; it was 6.2.", session()
    ).passed("absent")


def test_the_report_gives_intervals_per_measure():
    outcome = mcp_eval.Outcome(question=HB.id, kind="value", model="m", passed=True)
    outcome.score = score(HB, f"10.4 ({LINE})", session(LINE))
    text = mcp_eval.report([outcome])
    assert "| value | passed | 1/1 (100.0%" in text and "| value | located |" in text
    assert "1 questions; 0 ended without an answer." in text


# -------------------------------------------------------------------
# One exchange: a scripted model, the real server, a fake API
# -------------------------------------------------------------------
class Script:
    """A model that makes the given tool calls, then answers."""

    def __init__(self, calls, answer):
        self.turns = [[llm.ToolCall(n, a) for n, a in calls], answer]

    def __call__(self, messages, *, tools, max_tokens, model_role):
        assert {t["function"]["name"] for t in tools} >= {"find_patients", "get_item"}
        step = self.turns.pop(0)
        if isinstance(step, list):
            return llm.ToolTurn("m", model_role, "", tuple(step))
        return llm.ToolTurn("m", model_role, step, ())


def exchange(script, question, fake=None):
    fake = fake or FakeApi(flagged_record())
    client = ApiClient(
        Credentials("http://api.test", EMAIL, PASSWORD),
        transport=httpx.MockTransport(fake.handler),
    )

    async def go():
        async with Client(mcp_server.build_server(client)) as c:
            specs = mcp_eval.tool_specs(await c.list_tools())
            return await mcp_eval.ask(c, question, specs, "primary", chat=script)

    return anyio.run(go), fake


def test_an_exchange_reads_through_the_server_and_is_scored():
    script = Script(
        [
            ("list_documents", {"patient_id": PATIENT}),
            ("get_item", {"document_id": DOC, "item_id": "obs-1"}),
        ],
        f"Asha Rao's haemoglobin was 10.4 g/dL ({LINE}).",
    )
    outcome, fake = exchange(script, HB)
    assert outcome.error is None and outcome.passed, outcome.score
    assert outcome.tool_calls == [
        {"name": "list_documents", "ok": True},
        {"name": "get_item", "ok": True},
    ]
    assert [a["tool"] for a in fake.audit] == ["list_documents", "get_item"]


def test_an_answer_citing_lines_it_never_read_fails():
    script = Script(
        [("list_items", {"document_id": DOC})], f"It was 10.4 (document:{DOC}#p1:7-8)."
    )
    outcome, _ = exchange(script, HB)
    assert not outcome.score.grounded and not outcome.passed


def test_a_model_that_never_answers_runs_out_of_turns():
    class Loop:
        def __call__(self, messages, **_):
            return llm.ToolTurn(
                "m", "primary", "", (llm.ToolCall("list_items", {"document_id": DOC}),)
            )

    outcome, fake = exchange(Loop(), HB)
    assert outcome.error == "too_many_turns" and not outcome.passed
    assert len(fake.audit) == mcp_eval.MAX_TURNS


def test_tool_arguments_sent_as_json_text_are_read():
    script = Script(
        [("get_item", json.dumps({"document_id": DOC, "item_id": "obs-1"}))],
        f"10.4 ({LINE})",
    )
    outcome, _ = exchange(script, HB)
    assert outcome.tool_calls == [{"name": "get_item", "ok": True}]


# -------------------------------------------------------------------
# The command line
# -------------------------------------------------------------------
def test_without_live_it_only_counts_the_questions(capsys):
    assert mcp_eval.main([], environ={"MEDSUM_ENV": "dev"}) == 2
    assert "pass --live" in capsys.readouterr().out


@pytest.mark.parametrize("env", ["", "prod"])
def test_it_runs_only_in_development(env, capsys):
    assert mcp_eval.main(["--live"], environ={"MEDSUM_ENV": env}) == 2
    assert "MEDSUM_ENV=dev" in capsys.readouterr().err


def test_the_server_is_started_from_the_source_directory():
    params = mcp_eval.server_parameters({"MEDSUM_ENV": "dev"})
    assert params.args == ["-m", "medsum_mcp"] and params.cwd.endswith("src")
