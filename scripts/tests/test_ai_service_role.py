"""
The AI service's least-privilege role (db/ai_service_role.sql): the
service's own database work succeeds as that role, and what it has no
business with is refused.

The tests log in as a throwaway role built from the same file under
another name, so they never touch a medsum_ai role (or its password) that
already exists on this server. They skip when the test connection can't
create roles.
"""

import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

import document_jobs as jobs
import insights_service as service
import llm
import reset_db
from test_document_jobs import add_document, insight_row, replies_for
from test_service_jobs import add_processed, add_user, summary_row

ROLE = "medsum_ai"


def role_sql(name: str) -> str:
    """db/ai_service_role.sql, granting to `name` instead of medsum_ai."""
    return reset_db.AI_SERVICE_ROLE_FILE.read_text(encoding="utf-8").replace(ROLE, name)


def _as(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    netloc = f"{quote(user)}:{quote(password)}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


@pytest.fixture
def role(db, database_url):
    """A role with medsum_ai's grants; yields (name, its DATABASE_URL)."""
    name = f"medsum_ai_test_{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    with db.cursor() as cur:
        cur.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles"
                    " WHERE rolname = current_user")  # fmt: skip
        if not cur.fetchone()[0]:
            pytest.skip("the test connection can't create roles")
        cur.execute(role_sql(name))
        cur.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(name), sql.Literal(password)
            )
        )
    try:
        yield name, _as(database_url, name, password)
    finally:
        with db.cursor() as cur:
            cur.execute(f"DROP OWNED BY {name}")
            cur.execute(f"DROP ROLE {name}")


@pytest.fixture
def as_role(role, monkeypatch):
    """The service connects as the role; yields a connection as the role."""
    name, url = role
    monkeypatch.setenv("DATABASE_URL", url)
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


def test_the_role_file_creates_a_role_that_is_not_privileged(db, role):
    name, _ = role
    with db.cursor() as cur:
        cur.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin"
                    " FROM pg_roles WHERE rolname = %s", (name,))  # fmt: skip
        assert cur.fetchone() == (False, False, False, True)


def test_a_document_job_runs_as_the_role(db, as_role, model_client):
    doc = add_document(db)
    jobs.enqueue(as_role, doc)
    job = jobs.claim_next(as_role, "A")
    replies_for(model_client)

    jobs.process(job, "A")

    status, html, error = insight_row(db, doc)
    assert (status, error) == ("verified", None)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM langgraph.checkpoints")
        assert cur.fetchone()[0] > 0


def test_the_patient_summary_runs_as_the_role(db, as_role, model_client):
    user = add_user(db)
    add_processed(db, user, "d09", "2024-09-06 10:00+05:30")
    model_client.replies[llm.PRIMARY_MODEL] = ["The latest report flags TSH."]

    claim = service.claim_patient_summary(user)
    service.run_patient_summary(user, claim)

    html, status, error = summary_row(db, user)
    assert (status, error) == ("completed", None)


def test_the_checkpoint_setup_runs_as_the_role(as_role, role):
    from pipeline import checkpoints

    checkpoints.setup(role[1])


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT password_hash FROM users",
        "SELECT email FROM users",
        "SELECT * FROM sessions",
        "SELECT * FROM one_time_tokens",
        "SELECT * FROM organizations",
        "UPDATE users SET full_name = 'x'",
        "UPDATE documents SET storage_path = '/elsewhere'",
        "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
        " VALUES (gen_random_uuid(), 'a', 'b', 'c')",
        "DELETE FROM documents",
        "DELETE FROM pipeline_runs",
        "TRUNCATE jobs",
        "DROP TABLE jobs",
        "ALTER TABLE insights ADD COLUMN x int",
        "CREATE TABLE public.extra (id int)",
    ],
)
def test_what_the_service_has_no_business_with_is_refused(db, as_role, statement):
    add_document(db)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_role.execute(statement)


def test_a_reset_grants_the_role_again(db, database_url, role, monkeypatch, tmp_path):
    name, url = role
    role_file = tmp_path / "role.sql"
    role_file.write_text(role_sql(name), encoding="utf-8")
    monkeypatch.setattr(reset_db, "AI_SERVICE_ROLE_FILE", role_file)
    monkeypatch.setattr(reset_db, "AI_SERVICE_ROLE", name)

    assert reset_db.reset_database(database_url) is True

    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone() == (0,)


def test_a_reset_without_the_role_grants_nothing(database_url, monkeypatch):
    monkeypatch.setattr(reset_db, "AI_SERVICE_ROLE", "medsum_no_such_role")

    assert reset_db.reset_database(database_url) is False
