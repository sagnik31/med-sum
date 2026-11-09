"""
The observability layer inside a LangGraph graph: nodes wrapped in
`traced` see the run's context variables in whatever thread LangGraph runs
them, parallel branches included, so every node attempt becomes a step of
the run, model calls are reported to it, and a run resumed from a
checkpoint goes on numbering its steps.
"""

import operator
import threading
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

import llm
from db_rows import add_account
from observability import RunError, current_run, current_step, resume_run, start_run
from observability import context, traced
from pipeline import checkpoints

GRAPH_VERSION = "test-graph-1"


class State(TypedDict):
    seen: Annotated[list[dict], operator.add]


@pytest.fixture
def document_id(db) -> str:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, 'a.pdf', 'application/pdf', '/x') RETURNING id",
            (add_account(db),),
        )
        return str(cur.fetchone()[0])


def steps(db, run_id) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT node, seq, attempt, status, model, model_role FROM pipeline_steps"
            " WHERE run_id = %s ORDER BY seq",
            (run_id,),
        )
        return cur.fetchall()


def observing(name: str):
    """A traced node that records what it saw of the run and step."""

    @traced(name, input_of=lambda s: {"seen": len(s["seen"])})
    def node(state):
        run, step = current_run(), current_step()
        return {
            "seen": [
                {
                    "node": name,
                    "run": run.id if run else None,
                    "step_node": step.node if step else None,
                    "step_id": step.id if step else None,
                    "thread": threading.get_ident(),
                }
            ]
        }

    return node


def test_sequential_nodes_see_the_run_and_their_own_step(db, document_id):
    builder = StateGraph(State)
    builder.add_node("classify", observing("classify"))
    builder.add_node("parse", observing("parse"))
    builder.add_edge(START, "classify")
    builder.add_edge("classify", "parse")
    builder.add_edge("parse", END)
    graph = builder.compile()

    with start_run(document_id, GRAPH_VERSION) as run:
        result = graph.invoke({"seen": []})
        run.finish("verified")

    seen = result["seen"]
    assert [s["node"] for s in seen] == ["classify", "parse"]
    assert all(s["run"] == run.id for s in seen)
    assert [s["step_node"] for s in seen] == ["classify", "parse"]
    assert [row[:4] for row in steps(db, run.id)] == [
        ("classify", 1, 1, "ok"),
        ("parse", 2, 1, "ok"),
    ]


def test_parallel_branches_in_other_threads_see_the_run(db, document_id):
    # Two nodes in the same superstep run in LangGraph's thread pool.
    gate = threading.Barrier(2, timeout=10)

    def branch(name):
        inner = observing(name)

        def node(state):
            gate.wait()  # both are running at once, so in different threads
            return inner(state)

        return node

    builder = StateGraph(State)
    builder.add_node("page_1", traced("transcribe", page=1)(branch("page_1")))
    builder.add_node("page_2", traced("transcribe", page=2)(branch("page_2")))
    builder.add_node("join", observing("join"))
    builder.add_edge(START, "page_1")
    builder.add_edge(START, "page_2")
    builder.add_edge(["page_1", "page_2"], "join")
    builder.add_edge("join", END)
    graph = builder.compile()

    with start_run(document_id, GRAPH_VERSION) as run:
        result = graph.invoke({"seen": []})
        run.finish("verified")

    by_node = {s["node"]: s for s in result["seen"]}
    assert set(by_node) == {"page_1", "page_2", "join"}
    assert all(s["run"] == run.id for s in by_node.values())
    assert by_node["page_1"]["thread"] != by_node["page_2"]["thread"]

    rows = steps(db, run.id)
    assert sorted(r[1] for r in rows) == list(range(1, 6))  # 5 distinct numbers
    assert all(r[3] == "ok" for r in rows)
    # Each branch's inner step is nested in its transcribe step.
    with db.cursor() as cur:
        cur.execute(
            "SELECT child.node, child.page IS NULL, parent.node, parent.page"
            " FROM pipeline_steps child JOIN pipeline_steps parent"
            "  ON parent.id = child.parent_step_id"
            " WHERE child.run_id = %s ORDER BY parent.page",
            (run.id,),
        )
        assert cur.fetchall() == [
            ("page_1", True, "transcribe", 1),
            ("page_2", True, "transcribe", 2),
        ]


def test_a_model_call_in_a_node_is_reported_to_its_step(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = ["lab_report"]

    @traced("classify")
    def classify(state):
        return {"seen": [{"label": llm.chat([{"role": "user", "content": "x"}],
                                            max_tokens=10)}]}  # fmt: skip

    builder = StateGraph(State)
    builder.add_node("classify", classify)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", END)

    with start_run(document_id, GRAPH_VERSION) as run:
        result = builder.compile().invoke({"seen": []})
        run.finish("verified")

    assert result["seen"] == [{"label": "lab_report"}]
    assert steps(db, run.id) == [
        ("classify", 1, 1, "ok", llm.FALLBACK_MODEL, "fallback")
    ]


def test_a_graph_run_outside_a_pipeline_run_is_refused(db):
    builder = StateGraph(State)
    builder.add_node("classify", observing("classify"))
    builder.add_edge(START, "classify")
    builder.add_edge("classify", END)

    with pytest.raises(RunError, match="outside a pipeline run"):
        builder.compile().invoke({"seen": []})


def test_a_resumed_run_continues_from_the_checkpoint(db, database_url, document_id):
    crash = {"next_time": True}

    @traced("parse")
    def parse(state):
        if crash["next_time"]:
            crash["next_time"] = False
            raise RuntimeError("the process died here")
        return {"seen": [{"node": "parse"}]}

    def build(saver):
        builder = StateGraph(State)
        builder.add_node("classify", observing("classify"))
        builder.add_node("parse", parse)
        builder.add_edge(START, "classify")
        builder.add_edge("classify", "parse")
        builder.add_edge("parse", END)
        return builder.compile(checkpointer=saver)

    # First process: the run is open when parse fails, and is never ended.
    run = start_run(document_id, GRAPH_VERSION)
    config = {"configurable": {"thread_id": run.id}}
    token = context.current_run.set(run)
    try:
        with checkpoints.open_checkpointer(database_url) as saver:
            with pytest.raises(RuntimeError, match="died"):
                build(saver).invoke({"seen": []}, config)
    finally:
        context.current_run.reset(token)
        run.close()

    # Second process: reopen the run and resume the graph from its checkpoint.
    with resume_run(run.id) as resumed:
        with checkpoints.open_checkpointer(database_url) as saver:
            result = build(saver).invoke(None, config)
        resumed.finish("verified")

    assert [s["node"] for s in result["seen"]] == ["classify", "parse"]
    assert [row[:4] for row in steps(db, run.id)] == [
        ("classify", 1, 1, "ok"),
        ("parse", 2, 1, "failed"),
        ("parse", 3, 2, "ok"),  # classify did not run again
    ]
