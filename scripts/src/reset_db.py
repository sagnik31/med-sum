"""
Reset the development database and the uploaded files (step 6 of the
plan). The database holds synthetic data only (decision 16), so a schema
change is applied by resetting, not migrating.

From scripts/src:

    python -m reset_db         # says what it would delete; deletes nothing
    python -m reset_db --yes   # deletes it

It drops every table in the database's public schema and the LangGraph
checkpoint schema, loads db/schema.sql and creates the checkpoint tables,
then deletes the files in the uploads folder. data/input/
and data/output/ are never touched. Only a database on this machine can be
reset. Nothing it prints holds patient content: counts only.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

from pipeline import checkpoints

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = PROJECT_ROOT / "db" / "schema.sql"
UPLOADS_DIR = PROJECT_ROOT / "backend" / "uploads"
DEFAULT_URL = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}

# Schemas whose contents a reset removes: the application's tables and the
# LangGraph checkpoints.
RESET_SCHEMAS = ("public", checkpoints.CHECKPOINT_SCHEMA)


class NotLocal(Exception):
    pass


def check_local(url: str) -> None:
    """Refuse any database that isn't on this machine."""
    host = urlsplit(url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise NotLocal(f"refusing to reset a database on {host!r}: local only")


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/") or "(default)"


@dataclass
class Plan:
    database: str
    rows: dict[str, int]  # table (schema-qualified outside public) -> row count
    uploads: Path
    files: int


def plan(url: str, uploads: Path) -> Plan:
    """What a reset would delete."""
    check_local(url)
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = ANY(%s) AND table_type = 'BASE TABLE' "
            "ORDER BY table_schema <> 'public', table_schema, table_name",
            (list(RESET_SCHEMAS),),
        )
        tables = cur.fetchall()
        rows = {}
        for schema, table in tables:
            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            label = table if schema == "public" else f"{schema}.{table}"
            rows[label] = cur.fetchone()[0]
    return Plan(database_name(url), rows, uploads, len(_upload_files(uploads)))


def load_schema(conn) -> None:
    """Create every table from db/schema.sql, on a connection with an empty schema."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def reset_database(url: str) -> None:
    """
    Drop everything in the reset schemas and load db/schema.sql, in one
    transaction; then create the checkpoint tables, which need a
    connection of their own (their indexes are built concurrently).
    """
    check_local(url)
    # Leaving the block commits and closes the connection (psycopg 3).
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for schema in RESET_SCHEMAS:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
        load_schema(conn)
    checkpoints.setup(url)


def _upload_files(uploads: Path) -> list[Path]:
    if not uploads.is_dir():
        return []
    return [p for p in uploads.iterdir() if p.is_file()]


def clear_uploads(uploads: Path) -> int:
    """Delete the files in the uploads folder (not the folder); returns how many."""
    files = _upload_files(uploads)
    for path in files:
        path.unlink()
    return len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m reset_db", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--yes", action="store_true", help="really delete")
    parser.add_argument(
        "--database-url", default=os.environ.get("DATABASE_URL", DEFAULT_URL)
    )
    parser.add_argument("--uploads", type=Path, default=UPLOADS_DIR)
    args = parser.parse_args(argv)

    try:
        p = plan(args.database_url, args.uploads)
    except NotLocal as e:
        print(e, file=sys.stderr)
        return 2
    print(f"Database {p.database}:")
    for table, count in p.rows.items():
        print(f"  {table}: {count} rows")
    if not p.rows:
        print("  no tables")
    print(f"Uploads {p.uploads}: {p.files} files")

    if not args.yes:
        print("Nothing deleted. Run with --yes to drop the tables, load "
              "db/schema.sql and delete the uploaded files.")  # fmt: skip
        return 0
    reset_database(args.database_url)
    deleted = clear_uploads(args.uploads)
    print(f"Reset {p.database} from db/schema.sql; deleted {deleted} uploaded files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
