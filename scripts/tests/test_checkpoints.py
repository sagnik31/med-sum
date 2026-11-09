"""
LangGraph checkpoints: their tables are in a schema of their own, created
by setup() and by a reset, and they are enough for a graph whose node
failed to resume from its last finished node on a new connection.
"""

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

import reset_db
from pipeline import checkpoints

CHECKPOINT_TABLES = {
    "checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes",
}  # fmt: skip


def tables_in(db, schema: str) -> set[str]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (schema,),
        )
        return {name for (name,) in cur.fetchall()}


def count(db, sql: str, *params) -> int:
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


class State(TypedDict):
    visited: Annotated[list[str], operator.add]


def two_node_graph(first, second):
    builder = StateGraph(State)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    return builder


def thread(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def test_the_checkpoint_tables_are_in_their_own_schema(db):
    assert checkpoints.CHECKPOINT_SCHEMA == "langgraph"
    assert tables_in(db, "langgraph") == CHECKPOINT_TABLES
    assert not CHECKPOINT_TABLES & tables_in(db, "public")


def test_setup_can_run_again(db, database_url):
    migrations = "SELECT count(*) FROM langgraph.checkpoint_migrations"
    before = count(db, migrations)

    checkpoints.setup(database_url)
    checkpoints.setup(database_url)

    assert before > 0 and count(db, migrations) == before
    assert tables_in(db, "langgraph") == CHECKPOINT_TABLES


def test_the_connection_sees_only_the_checkpoint_schema(database_url):
    with checkpoints.connect(database_url) as conn:
        row = conn.execute("SHOW search_path").fetchone()
        assert row == {"search_path": "langgraph"}
        assert conn.autocommit


def test_a_graph_resumes_after_a_failed_node_on_a_new_connection(db, database_url):
    calls = []
    crash = {"next_time": True}

    def first(state):
        calls.append("first")
        return {"visited": ["first"]}

    def second(state):
        calls.append("second")
        if crash["next_time"]:
            crash["next_time"] = False
            raise RuntimeError("the process died here")
        return {"visited": ["second"]}

    config = thread("resume-test")
    with checkpoints.open_checkpointer(database_url) as saver:
        graph = two_node_graph(first, second).compile(checkpointer=saver)
        with pytest.raises(RuntimeError, match="died"):
            graph.invoke({"visited": []}, config)

    # As a new process would: a new connection and a newly compiled graph.
    with checkpoints.open_checkpointer(database_url) as saver:
        graph = two_node_graph(first, second).compile(checkpointer=saver)
        assert graph.get_state(config).next == ("second",)
        result = graph.invoke(None, config)

    assert result == {"visited": ["first", "second"]}
    assert calls == ["first", "second", "second"]  # first ran once
    stored = "SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = %s"
    assert count(db, stored, "resume-test") > 0


def test_threads_are_kept_apart(db, database_url):
    def first(state):
        return {"visited": ["first"]}

    def second(state):
        return {"visited": ["second"]}

    with checkpoints.open_checkpointer(database_url) as saver:
        graph = two_node_graph(first, second).compile(checkpointer=saver)
        graph.invoke({"visited": []}, thread("a"))
        assert graph.get_state(thread("b")).values == {}
        assert graph.get_state(thread("a")).values == {"visited": ["first", "second"]}


def test_a_reset_empties_the_checkpoints_and_recreates_their_tables(db, database_url):
    def node(state):
        return {"visited": ["x"]}

    with checkpoints.open_checkpointer(database_url) as saver:
        two_node_graph(node, node).compile(checkpointer=saver).invoke(
            {"visited": []}, thread("before-reset")
        )
    assert count(db, "SELECT count(*) FROM langgraph.checkpoints") > 0

    reset_db.reset_database(database_url)

    assert tables_in(db, "langgraph") == CHECKPOINT_TABLES
    assert count(db, "SELECT count(*) FROM langgraph.checkpoints") == 0


def test_the_reset_plan_lists_the_checkpoint_tables(db, database_url, tmp_path):
    plan = reset_db.plan(database_url, tmp_path)

    assert "users" in plan.rows and "documents" in plan.rows
    assert {f"langgraph.{t}" for t in CHECKPOINT_TABLES} <= set(plan.rows)
    # public first, so the application's tables lead the printed list
    assert list(plan.rows)[0].count(".") == 0
    assert list(plan.rows)[-1].startswith("langgraph.")
