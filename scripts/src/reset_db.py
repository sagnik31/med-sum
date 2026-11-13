"""
Reset the development database and the uploaded files. The database holds
synthetic data only, so a schema change is applied by resetting, not
migrating. See docs/adr/0008-patient-data-protection.md.

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
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

import settings
from pipeline import checkpoints

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = PROJECT_ROOT / "db" / "schema.sql"
AI_SERVICE_ROLE_FILE = PROJECT_ROOT / "db" / "ai_service_role.sql"
AI_SERVICE_ROLE = "medsum_ai"
UPLOADS_DIR = PROJECT_ROOT / "backend" / "uploads"
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


def grant_ai_service_role(conn) -> None:
    """Create the AI service's role if missing, and grant its privileges."""
    with conn.cursor() as cur:
        cur.execute(AI_SERVICE_ROLE_FILE.read_text(encoding="utf-8"))


def reset_database(url: str) -> bool:
    """
    Drop everything in the reset schemas and load db/schema.sql, in one
    transaction; then create the checkpoint tables, which need a
    connection of their own (their indexes are built concurrently).

    Dropping the tables drops their grants, so when the AI service's role
    exists (db/ai_service_role.sql) its grants are applied again; returns
    whether they were.
    """
    check_local(url)
    # Leaving the block commits and closes the connection (psycopg 3).
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for schema in RESET_SCHEMAS:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
        load_schema(conn)
    checkpoints.setup(url)
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AI_SERVICE_ROLE,))
        if cur.fetchone() is None:
            return False
        grant_ai_service_role(conn)
    return True


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
        "--database-url",
        help="a role that owns the schema (default: DATABASE_URL, see settings.py)",
    )
    parser.add_argument("--uploads", type=Path, default=UPLOADS_DIR)
    args = parser.parse_args(argv)

    try:
        url = args.database_url or settings.database_url()
        p = plan(url, args.uploads)
    except (NotLocal, settings.SettingsError) as e:
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
    granted = reset_database(url)
    deleted = clear_uploads(args.uploads)
    print(f"Reset {p.database} from db/schema.sql; deleted {deleted} uploaded files.")
    if granted:
        print(
            f"Granted {AI_SERVICE_ROLE} its privileges again (db/ai_service_role.sql)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
