"""
The reset script: it lists before it deletes, deletes only with --yes,
only on this machine, and leaves a database built from db/schema.sql.
"""

import pytest

import reset_db
from db_rows import add_account


def tables(db) -> set[str]:
    with db.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'")  # fmt: skip
        return {name for (name,) in cur.fetchall()}


def count(db, table) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


@pytest.fixture
def uploads(tmp_path):
    folder = tmp_path / "uploads"
    folder.mkdir()
    for name in ("a.pdf", "b.png"):
        (folder / name).write_bytes(b"x")
    (folder / "keep").mkdir()  # folders are not uploads
    return folder


@pytest.fixture
def filled(db):
    with db.cursor() as cur:
        user = add_account(db)
        cur.execute("INSERT INTO documents (user_id, original_name, content_type, "
                    "storage_path) VALUES (%s, 'a.pdf', 'application/pdf', '/x')",
                    (user,))  # fmt: skip
    return db


EXPECTED_TABLES = {
    "organizations", "users", "sessions", "one_time_tokens", "documents",
    "insights", "pipeline_runs", "pipeline_steps", "pipeline_model_calls",
    "document_records",
}  # fmt: skip


def test_without_yes_nothing_is_deleted(filled, database_url, uploads, capsys):
    assert (
        reset_db.main(["--database-url", database_url, "--uploads", str(uploads)]) == 0
    )

    out = capsys.readouterr().out
    assert "users: 1 rows" in out and "documents: 1 rows" in out
    assert "2 files" in out and "Nothing deleted" in out
    assert count(filled, "documents") == 1
    assert len(list(uploads.iterdir())) == 3


def test_with_yes_the_data_and_uploads_are_gone(filled, database_url, uploads, capsys):
    args = ["--database-url", database_url, "--uploads", str(uploads), "--yes"]

    assert reset_db.main(args) == 0
    assert tables(filled) == EXPECTED_TABLES
    assert all(count(filled, t) == 0 for t in EXPECTED_TABLES)
    assert [p.name for p in uploads.iterdir()] == ["keep"]
    assert "deleted 2 uploaded files" in capsys.readouterr().out


def test_a_reset_drops_tables_the_schema_no_longer_has(db, database_url):
    with db.cursor() as cur:
        cur.execute("CREATE TABLE leftover (id int)")

    reset_db.reset_database(database_url)
    assert tables(db) == EXPECTED_TABLES


def test_a_reset_can_be_run_twice(db, database_url, tmp_path):
    reset_db.reset_database(database_url)
    reset_db.reset_database(database_url)

    assert tables(db) == EXPECTED_TABLES
    assert reset_db.clear_uploads(tmp_path / "missing") == 0


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@db.example.com:5432/med_sum",
     "postgres://u:p@10.0.0.5/med_sum"],
)  # fmt: skip
def test_only_a_local_database_is_reset(url, capsys, tmp_path):
    with pytest.raises(reset_db.NotLocal):
        reset_db.reset_database(url)
    assert (
        reset_db.main(["--database-url", url, "--uploads", str(tmp_path), "--yes"]) == 2
    )
    assert "local only" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    ["postgres://postgres:postgres@localhost:5432/med_sum",
     "postgres://postgres@127.0.0.1/med_sum", "postgresql:///med_sum"],
)  # fmt: skip
def test_local_databases_are_allowed(url):
    reset_db.check_local(url)


def test_the_default_paths_are_the_projects():
    assert reset_db.SCHEMA_FILE.is_file()
    assert reset_db.UPLOADS_DIR.parts[-2:] == ("backend", "uploads")
