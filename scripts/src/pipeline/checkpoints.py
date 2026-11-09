"""
LangGraph checkpoints: the graph's state after each node, saved by
langgraph-checkpoint-postgres so that a run that crashed can resume from
its last finished node.

The checkpoint tables live in a schema of their own (CHECKPOINT_SCHEMA) in
the service's database, apart from the application's tables in public.
db/schema.sql only creates that schema; the package creates and migrates
its own tables in it (setup()), and reset_db empties it with the rest.

The saved state is the graph's state, which holds document content, so
checkpoints are patient data like pipeline_steps: database only.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

CHECKPOINT_SCHEMA = "langgraph"

DEFAULT_URL = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"


def database_url() -> str:
    """DATABASE_URL (the same variable as the Go backend), read at call time."""
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def connect(url: str | None = None) -> psycopg.Connection:
    """
    A connection as PostgresSaver needs it (autocommit, dict rows, no
    prepared statements), whose search path is the checkpoint schema
    alone, so the saver's unqualified table names resolve there and
    nowhere else.
    """
    return psycopg.connect(
        url or database_url(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-c search_path={CHECKPOINT_SCHEMA}",
    )


@contextmanager
def open_checkpointer(url: str | None = None) -> Iterator[PostgresSaver]:
    """A checkpointer on its own connection, closed when the block ends."""
    with connect(url) as conn:
        yield PostgresSaver(conn)


def setup(url: str | None = None) -> None:
    """
    Create the checkpoint tables, or migrate them to the installed
    package's version. Safe to run again. The schema must exist (it is
    created by db/schema.sql).
    """
    with open_checkpointer(url) as saver:
        saver.setup()
