"""
Shared fixtures for the AI service tests.

- No test reaches Ollama: every test gets a fake model client, which fails
  the test on any call it was not told to expect.
- Tests marked `db` run against a throwaway database (med_sum_test_py),
  created from the schema files at the start of the session and dropped at
  the end. The development database (med_sum) is never touched.
"""

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

import llm

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILES = [
    PROJECT_ROOT / "db" / "migrations" / "med_sum_schema.sql",
    PROJECT_ROOT / "db" / "migrations" / "002_patient_summary_status.sql",
]
TEST_DB_NAME = "med_sum_test_py"
# Any database on the server will do; it is only used to create and drop the test one.
ADMIN_URL = os.environ.get(
    "MEDSUM_TEST_ADMIN_URL",
    "postgres://postgres:postgres@localhost:5432/postgres",
)


# -------------------------------------------------------------------
# Model client
# -------------------------------------------------------------------
class FakeModelClient:
    """
    Stands in for ollama.Client.

    `replies[model]` is a list consumed in order. Each reply is the reply
    text (finishes normally), a (text, done_reason) pair, or an exception
    to raise. Every call is recorded in `calls`.
    """

    def __init__(self):
        self.replies: dict[str, list] = {}
        self.calls: list[dict] = []

    def chat(self, *, model, messages, stream, think, options):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "stream": stream,
                "think": think,
                "options": options,
            }
        )
        queue = self.replies.get(model)
        if not queue:
            raise AssertionError(f"unexpected call to model {model!r}")
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        text, done_reason = reply if isinstance(reply, tuple) else (reply, "stop")
        return _stream_chunks(text, done_reason)


def _stream_chunks(text: str, done_reason: str):
    """Yield `text` a few characters at a time, like a streamed reply."""
    pieces = [text[i : i + 5] for i in range(0, len(text), 5)] or [""]
    for piece in pieces[:-1]:
        yield SimpleNamespace(
            message=SimpleNamespace(content=piece), done=False, done_reason=None
        )
    yield SimpleNamespace(
        message=SimpleNamespace(content=pieces[-1]),
        done=True,
        done_reason=done_reason,
    )


@pytest.fixture(autouse=True)
def model_client(monkeypatch) -> FakeModelClient:
    fake = FakeModelClient()
    monkeypatch.setattr(llm, "_client", fake)
    return fake


# -------------------------------------------------------------------
# Throwaway database
# -------------------------------------------------------------------
def pytest_collection_modifyitems(items):
    """Mark every test that uses the database, so `-m "not db"` skips them."""
    for item in items:
        if "db" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)


def _url_with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + name))


def _unavailable(reason: str):
    if os.environ.get("MEDSUM_REQUIRE_DB") == "1":
        pytest.fail(f"Database tests required but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def database_url():
    psql = shutil.which("psql")
    if not psql:
        _unavailable("psql is not on PATH")
    try:
        admin = psycopg2.connect(ADMIN_URL, connect_timeout=5)
    except psycopg2.OperationalError as e:
        _unavailable(f"Postgres is unreachable: {e}")
    admin.autocommit = True

    def drop():
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")

    drop()
    with admin.cursor() as cur:
        cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    url = _url_with_database(ADMIN_URL, TEST_DB_NAME)
    try:
        for schema in SCHEMA_FILES:
            subprocess.run(
                [psql, "-q", "-v", "ON_ERROR_STOP=1", "-d", url, "-f", str(schema)],
                check=True,
                capture_output=True,
                text=True,
            )
        yield url
    finally:
        drop()
        admin.close()


@pytest.fixture
def db(database_url, monkeypatch):
    """
    An autocommit connection to an empty test database. The service under
    test connects to the same database through DATABASE_URL.
    """
    monkeypatch.setenv("DATABASE_URL", database_url)
    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE users, documents, insights CASCADE")
    yield conn
    conn.close()
