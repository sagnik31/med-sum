"""
The trace screen's views (db/schema.sql: document_trace_steps and
org_pipeline_steps) say which steps `python -m pipeline.replay` can
rebuild. The rule is written in SQL for the API and in Python for the
command; these tests run real graph runs on the fake model and check the
two agree, step by step, including a repair, and that the admin view holds
no content.
"""

import json

import pytest

import llm
from pipeline.replay import ReplayError, replay
from test_replay import document, ran, repaired  # noqa: F401  (fixtures)


def _steps(db, view: str, columns: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            f"SELECT step_id::text AS step_id, node, {columns} FROM {view} ORDER BY seq"
        )
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _replays(step_id: str) -> bool:
    try:
        replay(step_id)
    except ReplayError:
        return False
    return True


def _any_reply(monkeypatch):
    """Every model call answers "{}" from now on: replay then fails only for
    its own reasons. Called after the run, which needs the real fake model."""
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: "{}")


@pytest.mark.parametrize("fixture", ["ran", "repaired"])
def test_replayable_in_the_views_is_what_replay_accepts(
    db, fixture, request, monkeypatch
):
    request.getfixturevalue(fixture)
    _any_reply(monkeypatch)
    steps = _steps(db, "document_trace_steps", "replayable")
    nodes = {s["node"] for s in steps}
    assert {"transcribe", "classify", "parse", "validate", "finish"} <= nodes
    if fixture == "repaired":
        assert "repair" in nodes
    for s in steps:
        assert s["replayable"] == _replays(s["step_id"]), s["node"]
    admin = {s["step_id"]: s["replayable"] for s in
             _steps(db, "org_pipeline_steps", "replayable")}  # fmt: skip
    assert admin == {s["step_id"]: s["replayable"] for s in steps}


def test_a_repair_with_tools_is_not_replayable_in_either(db, request, monkeypatch):
    request.getfixturevalue("repaired")
    _any_reply(monkeypatch)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE pipeline_steps SET prompt_name = prompt_name || '+tools'"
            " WHERE node = 'repair'"
        )
    for view in ("document_trace_steps", "org_pipeline_steps"):
        (step,) = [s for s in _steps(db, view, "replayable") if s["node"] == "repair"]
        assert step["replayable"] is False
        assert not _replays(step["step_id"])


def test_the_admin_views_hold_no_content(db, request):
    request.getfixturevalue("repaired")
    with db.cursor() as cur:
        cur.execute("SELECT * FROM org_pipeline_steps")
        steps = [
            dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()
        ]
        cur.execute("SELECT * FROM org_pipeline_runs")
        runs = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
        cur.execute("SELECT full_name, mrn FROM patients")
        identifiers = [v for row in cur.fetchall() for v in row]
    text = json.dumps({"steps": steps, "runs": runs}, default=str)
    columns = {c for row in steps + runs for c in row}
    assert not columns & {"input", "output", "findings", "error", "document_id"}
    assert '"message"' not in text  # failures keep no finding's message
    for value in identifiers:
        assert value not in text
    # The repair's problems (finding messages) never reach the admin view.
    assert "999 mg" not in text
