"""
Structured JSON logs, one object per line, on the console and in a file.

Every line carries the ids of the run and step it was written in (from
observability.context), so any log call inside a node, llm.chat's
included, can be matched to its pipeline_steps row.

Never patient content: a line holds the timestamp, level, logger, the
message (written by code), the current ids and only the fields named in
LOG_FIELDS from `extra={"obs": {...}}`; anything else passed there is
dropped. An exception is logged as its type and stack, without its
message, which may quote a document. Inputs, outputs, findings and error
messages live in the database only.
"""

import json
import logging
import logging.handlers
import os
import sys
import traceback
from datetime import datetime, timezone

from observability import context
from observability.jsonable import jsonable

# The fields a log line may carry from `extra={"obs": ...}`.
LOG_FIELDS = frozenset(
    {
        "event",
        "run_id",
        "trace_id",
        "document_id",
        "step_id",
        "span_id",
        "parent_span_id",
        "node",
        "seq",
        "attempt",
        "page",
        "status",
        "end_state",
        "duration_ms",
        "graph_version",
        "model",
        "model_role",
        "model_calls",
        "models_used",
        "prompt_name",
        "prompt_hash",
        "prompt_tokens",
        "completion_tokens",
        "findings",  # counts per severity, never the findings
        "error_type",
        "steps_left_running",
        "steps_interrupted",
        # The service's job lines (insights_service): ids and sizes only.
        "user_id",
        "documents",
        "markdown_chars",
        "html_chars",
    }
)

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUPS = 5


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Formatting happens in the logging thread for the handlers below,
        # so the context is the caller's (a QueueHandler would lose it).
        entry.update(context.current_ids())
        fields = getattr(record, "obs", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key in LOG_FIELDS and value is not None:
                    entry[key] = jsonable(value)
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, _, tb = record.exc_info
            entry["exc_type"] = exc_type.__name__
            entry["stack"] = [
                f"{os.path.basename(f.filename)}:{f.lineno} in {f.name}"
                for f in traceback.extract_tb(tb)
            ]
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(
    *,
    log_file: str | None = None,
    console: bool = True,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
) -> list[logging.Handler]:
    """
    Send the root logger's output as JSON lines to stderr and, when
    `log_file` is given, to that file (rotated at `max_bytes`). Replaces
    the handlers an earlier call added; other handlers stay.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_medsum_json", False):
            root.removeHandler(handler)
            handler.close()
    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
        )
    formatter = JsonFormatter()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler._medsum_json = True
        root.addHandler(handler)
    root.setLevel(level)
    return handlers
