"""
Shared fixtures for the AI service tests.

- No test reaches Ollama: every test gets a fake model client, which fails
  the test on any call it was not told to expect.
- Tests marked `db` run against a throwaway database (med_sum_test_py),
  created from db/schema.sql at the start of the session and dropped at
  the end. The development database (med_sum) is never touched.
"""

import io
import logging
import os
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

import llm
import model_health
import reset_db
from observability import JsonFormatter
from pipeline import checkpoints

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
    text (finishes normally), a (text, done_reason) pair, a (text,
    done_reason, fields) triple whose fields (token counts, durations) go
    on the final chunk, or an exception to raise. Every call is recorded in
    `calls`. `list()` answers from `digests` (model -> digest), or raises
    `list_error` if it is set.
    """

    def __init__(self):
        self.replies: dict[str, list] = {}
        self.calls: list[dict] = []
        self.digests: dict[str, str] = {}
        self.list_error: BaseException | None = None
        self.list_calls = 0

    def list(self):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        models = [SimpleNamespace(model=m, digest=d) for m, d in self.digests.items()]
        return SimpleNamespace(models=models)

    def chat(self, *, model, messages, stream, think, options, format=None):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "stream": stream,
                "think": think,
                "options": options,
                "format": format,
            }
        )
        queue = self.replies.get(model)
        if not queue:
            raise AssertionError(f"unexpected call to model {model!r}")
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if not isinstance(reply, tuple):
            reply = (reply, "stop")
        text, done_reason, *fields = reply
        return _stream_chunks(text, done_reason, fields[0] if fields else {})


def _stream_chunks(text: str, done_reason: str, fields: dict):
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
        **fields,
    )


@pytest.fixture(autouse=True)
def no_job_worker(monkeypatch):
    """The service starts no job worker in tests; job tests run one themselves."""
    monkeypatch.setenv("MEDSUM_WORKER", "0")


@pytest.fixture(autouse=True)
def fresh_model_health():
    """Every test starts with every model's circuit breaker closed."""
    model_health.reset()
    yield
    model_health.reset()


@pytest.fixture(autouse=True)
def model_client(monkeypatch) -> FakeModelClient:
    fake = FakeModelClient()
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "_digests", {})
    return fake


# -------------------------------------------------------------------
# Logs
# -------------------------------------------------------------------
@pytest.fixture
def json_log():
    """Everything logged during the test, formatted as the service will."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield out
    root.removeHandler(handler)
    root.setLevel(old_level)


@pytest.fixture
def root_logger():
    """Put the root logger back as it was after configure_logging."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield root
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


# -------------------------------------------------------------------
# Throwaway database
# -------------------------------------------------------------------
def pytest_collection_modifyitems(items):
    """Mark every test that uses the database, so `-m "not db"` skips them."""
    for item in items:
        if {"db", "database_url"} & set(getattr(item, "fixturenames", ())):
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
    try:
        admin = psycopg.connect(ADMIN_URL, connect_timeout=5, autocommit=True)
    except psycopg.OperationalError as e:
        _unavailable(f"Postgres is unreachable: {e}")

    def drop():
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")

    drop()
    with admin.cursor() as cur:
        cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    url = _url_with_database(ADMIN_URL, TEST_DB_NAME)
    try:
        with psycopg.connect(url) as conn:  # commits and closes
            reset_db.load_schema(conn)
        checkpoints.setup(url)
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
    conn = psycopg.connect(database_url, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE organizations, users, documents, insights CASCADE")
        cur.execute(
            "TRUNCATE langgraph.checkpoints, langgraph.checkpoint_blobs,"
            " langgraph.checkpoint_writes"
        )
    yield conn
    conn.close()
