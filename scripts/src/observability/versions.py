"""
What a pipeline run was made with, stored on its pipeline_runs row so a
result can be traced to the code and database that produced it:

- code_version(): the git commit the code is at, and whether the working
  tree had changes. MEDSUM_CODE_VERSION overrides it where there is no git
  checkout (a container image, say); unknown is None, never a guess.
- output_schema_hash(): a SHA-256 of the JSON schemas the pipeline sends
  as the models' structured output (the classifier's and each parse's).
- db_schema_hash(cur): a SHA-256 of the database's own table structure
  (each public table and view, its columns, their types and nullability),
  read from pg_catalog so every role sees the same thing. Two runs with the
  same hash ran on the same structure, whatever schema file was loaded.

See docs/adr/0035-run-provenance-and-replay.md.
"""

import functools
import hashlib
import json
import os
import subprocess
from pathlib import Path

CODE_VERSION_ENV = "MEDSUM_CODE_VERSION"
_SRC_DIR = Path(__file__).resolve().parent.parent

SCHEMA_QUERY = """
SELECT c.relname, c.relkind::text, a.attname,
       format_type(a.atttypid, a.atttypmod), a.attnotnull
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


def code_version(cwd: Path = _SRC_DIR) -> dict | None:
    """The git commit the code is at, and whether the tree has changes."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True,
            check=True,
        ).stdout.strip()  # fmt: skip
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True,
            text=True, check=True,
        ).stdout  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit, "dirty": bool(status.strip())}


@functools.cache
def _process_code_version() -> dict | None:
    # Once per process: the code a process runs is the code it loaded.
    return code_version()


def run_code_version(environ=os.environ) -> tuple[str | None, bool | None]:
    """(commit, dirty) for a run's row; the override has no dirty flag."""
    override = environ.get(CODE_VERSION_ENV, "").strip()
    if override:
        return override, None
    found = _process_code_version()
    if found is None:
        return None, None
    return found["commit"], found["dirty"]


@functools.cache
def output_schema_hash() -> str:
    """The SHA-256 of every structured-output schema the models are sent."""
    from record.printed import PARSE_MODELS  # here: record imports observability
    from record.schemas import classifier_schema, parse_output_schema

    schemas = {"classify": classifier_schema()}
    for doc_type in sorted(PARSE_MODELS, key=lambda t: t.value):
        schemas[f"parse_{doc_type.value}"] = parse_output_schema(doc_type)
    text = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def db_schema_hash(cur) -> str:
    """The SHA-256 of the public schema's tables, views and columns."""
    cur.execute(SCHEMA_QUERY)
    h = hashlib.sha256()
    for row in cur.fetchall():
        h.update("\t".join(str(v) for v in row).encode())
        h.update(b"\n")
    return h.hexdigest()
