"""
Pipeline runs and steps: one pipeline_runs row per run, one
pipeline_steps row per node attempt. See docs/adr/0006-durable-traceable-runs.md.

The names follow OpenTelemetry so an exporter can be added later: a run is
a trace (pipeline_runs.trace_id), a step is a span whose span id is its
pipeline_steps.id, and parent_step_id is the parent span.

    with start_run(document_id, graph_version="1") as run:
        parse = traced("parse", input_of=lambda s: s["pages"])(parse_node)
        ...                         # the graph calls parse(state)
        run.finish("needs_review")

A node wrapped with `traced` can only run inside a run, and each call
writes its step row before the node starts (status running) and closes it
when the node returns (ok) or raises (failed). Inside a node,
`current_step()` gives the step, to record the prompt, validator findings
or a failure that isn't an exception; model, role and token counts come
from llm.chat on their own.

Inputs, outputs, findings and error messages go to the database only. The
log lines written here carry ids, node names, statuses, timings, model
names and counts, never document content.

Independent of LangGraph: `traced` wraps any callable whose first argument
is the node's state.
"""

import functools
import hashlib
import logging
import threading
import time
from collections import Counter
from contextlib import contextmanager

import psycopg
from psycopg.types.json import Jsonb

import settings

from observability import context, model_calls, versions
from observability.redact import redact, redact_finding
from observability.jsonable import jsonable
from record.common import Provenance

logger = logging.getLogger("medsum.pipeline")

END_STATES = ("verified", "needs_review", "failed")
RUNNING, OK, FAILED = "running", "ok", "failed"

LEFT_RUNNING_AT_END = "the run ended while this step was still running"
LEFT_RUNNING_AT_RESUME = "interrupted: the run was resumed while this step was running"
NO_END_STATE = "the run ended without an end state"

# Models in the order the run first used them.
_MODELS_USED = (
    "ARRAY(SELECT model FROM pipeline_steps"
    " WHERE run_id = %(run)s AND model IS NOT NULL"
    " GROUP BY model ORDER BY min(seq))"
)

# A run's model-call totals (pipeline_run_model_totals), computed from the
# call rows when it ends, logged with run_ended and kept on Run.model_totals.
RUN_TOTALS = (
    "model_calls", "fallback_calls", "capped_calls", "failed_calls",
    "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens",
    "model_wall_ms", "model_total_ms",
)  # fmt: skip

_UNSET = object()


class RunError(RuntimeError):
    """The run or step can't be used as asked (not open, already ended, ...)."""


def prompt_hash(text: str) -> str:
    """The hash stored with a prompt's name: "sha256:" and the hex digest."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_connect():
    return psycopg.connect(settings.database_url())


def _error_text(exc: BaseException) -> str:
    """For the database only: the message may quote document content."""
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _ms_since(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


# -------------------------------------------------------------------
# Runs
# -------------------------------------------------------------------
def start_run(document_id: str, graph_version: str, *, connect=None) -> "Run":
    """Insert a pipeline_runs row and return the run; use it as a context manager."""
    conn = _open(connect)
    commit, dirty = versions.run_code_version()
    try:
        with conn.transaction(), conn.cursor() as cur:
            db_schema = versions.db_schema_hash(cur)
            cur.execute(
                "INSERT INTO pipeline_runs (document_id, graph_version,"
                " code_version, code_dirty, schema_hash, db_schema_hash)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, trace_id",
                (document_id, graph_version, commit, dirty,
                 versions.output_schema_hash(), db_schema),
            )  # fmt: skip
            run_id, trace_id = cur.fetchone()
    except BaseException:
        conn.close()
        raise
    run = Run(conn, run_id, trace_id, str(document_id), graph_version)
    logger.info(
        "run started",
        extra={
            "obs": {
                "event": "run_started",
                "run_id": run.id,
                "trace_id": run.trace_id,
                "document_id": run.document_id,
                "graph_version": graph_version,
            }
        },
    )
    return run


def resume_run(run_id: str, *, connect=None) -> "Run":
    """
    Reopen a run that hasn't ended, e.g. after a crash, when the graph
    resumes from its checkpoint. Steps left running by the crashed process
    are closed as failed; new steps continue the run's sequence.
    """
    conn = _open(connect)
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT trace_id, document_id, graph_version, ended_at"
                " FROM pipeline_runs WHERE id = %s FOR UPDATE",
                (run_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise RunError(f"no pipeline run {run_id}")
            trace_id, document_id, graph_version, ended_at = row
            if ended_at is not None:
                raise RunError(f"pipeline run {run_id} has already ended")
            cur.execute(
                "UPDATE pipeline_steps SET status = 'failed', ended_at = now(),"
                " error = coalesce(error, %s)"
                " WHERE run_id = %s AND status = 'running'",
                (LEFT_RUNNING_AT_RESUME, run_id),
            )
            interrupted = cur.rowcount
    except BaseException:
        conn.close()
        raise
    run = Run(conn, str(run_id), trace_id, str(document_id), graph_version)
    logger.info(
        "run resumed",
        extra={
            "obs": {
                "event": "run_resumed",
                "run_id": run.id,
                "trace_id": run.trace_id,
                "document_id": run.document_id,
                "graph_version": graph_version,
                "steps_interrupted": interrupted,
            }
        },
    )
    return run


def _open(connect):
    conn = (connect or _default_connect)()
    # Every write is its own explicit transaction (conn.transaction(), see
    # Run._tx), which holds the row lock that numbers steps until it ends.
    # Autocommit keeps reads outside those blocks from leaving a
    # transaction open.
    conn.autocommit = True
    return conn


class Run:
    """
    An open pipeline run. As a context manager it makes itself the current
    run, and on leaving it ends the run as failed unless finish() was called
    (with the exception's message as the run's error, if there was one).
    """

    def __init__(self, conn, run_id, trace_id, document_id, graph_version):
        self.conn = conn
        self.id = str(run_id)
        self.trace_id = trace_id
        self.document_id = document_id
        self.graph_version = graph_version
        self.ended = False
        self.end_state: str | None = None
        self.model_totals: dict = {}  # set when the run ends
        # Circuit breaker switches during this run's model calls (signal
        # OP-3), in order. Kept in memory, like the breaker itself.
        self.breaker_switches: list[dict] = []
        self._lock = threading.Lock()  # parallel nodes share the connection
        self._started = time.monotonic()
        self._tokens: list = []
        # The patient identifiers this run has seen, masked in the findings
        # and error text it stores (observability.redact).
        self.identifiers: set[str] = set()

    def add_identifiers(self, *values) -> None:
        """Patient identifiers to mask in what this run stores from now on."""
        with self._lock:
            self.identifiers.update(str(v) for v in values if v)

    def redacted(self, text: str | None) -> str | None:
        return redact(text, self.identifiers)

    def note_breaker_switch(self, event: str, model: str, role: str) -> None:
        with self._lock:
            self.breaker_switches.append(
                {"event": event, "model": model, "model_role": role}
            )

    # Context manager -------------------------------------------------
    def __enter__(self) -> "Run":
        self._tokens.append(context.current_run.set(self))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if not self.ended:
                self.finish(
                    "failed",
                    error=_error_text(exc) if exc is not None else NO_END_STATE,
                )
        finally:
            context.current_run.reset(self._tokens.pop())
            self.close()
        return False

    def close(self) -> None:
        if not self.conn.closed:
            self.conn.close()

    @contextmanager
    def _tx(self):
        """One transaction on the run's connection, one thread at a time."""
        with self._lock, self.conn.transaction(), self.conn.cursor() as cur:
            yield cur

    # Ending ------------------------------------------------------------
    def finish(self, end_state: str, *, error: str | None = None) -> None:
        """
        End the run with one of END_STATES. Steps still running are closed
        as failed. `error` is stored in the database and never logged.
        """
        if end_state not in END_STATES:
            raise ValueError(
                f"end state must be one of {END_STATES}, not {end_state!r}"
            )
        if self.ended:
            raise RunError(f"pipeline run {self.id} has already ended")
        with self._tx() as cur:
            cur.execute(
                "UPDATE pipeline_steps SET status = 'failed', ended_at = now(),"
                " error = coalesce(error, %(why)s)"
                " WHERE run_id = %(run)s AND status = 'running'",
                {"run": self.id, "why": LEFT_RUNNING_AT_END},
            )
            left_running = cur.rowcount
            cur.execute(
                f"UPDATE pipeline_runs SET end_state = %(state)s, ended_at = now(),"
                f" error = %(error)s, models_used = {_MODELS_USED}"
                f" WHERE id = %(run)s AND ended_at IS NULL RETURNING models_used",
                {"run": self.id, "state": end_state, "error": self.redacted(error)},
            )
            row = cur.fetchone()
            if row is None:
                raise RunError(f"pipeline run {self.id} has already ended")
            totals = self._model_totals(cur)
        self.model_totals = totals
        self.ended = True
        self.end_state = end_state
        level = logging.ERROR if end_state == "failed" else logging.INFO
        logger.log(
            level,
            "run ended: %s",
            end_state,
            extra={
                "obs": {
                    "event": "run_ended",
                    "run_id": self.id,
                    "trace_id": self.trace_id,
                    "document_id": self.document_id,
                    "end_state": end_state,
                    "duration_ms": _ms_since(self._started),
                    "models_used": list(row[0]),
                    "steps_left_running": left_running,
                    **totals,
                }
            },
        )

    def _model_totals(self, cur) -> dict:
        """The run's model-call totals, from the pipeline_run_model_totals view."""
        cur.execute(
            f"SELECT {', '.join(RUN_TOTALS)} FROM pipeline_run_model_totals"
            " WHERE run_id = %s",
            (self.id,),
        )
        values = cur.fetchone()
        return dict(zip(RUN_TOTALS, values)) if values else {}

    # Steps -------------------------------------------------------------
    @contextmanager
    def step(
        self,
        node: str,
        *,
        page: int | None = None,
        attempt: int | None = None,
        input=_UNSET,
        parent_id=_UNSET,
    ):
        """
        Record one node attempt around the block. `attempt` defaults to one
        more than the run's earlier steps of the same node and page;
        `parent_id` to the step this one runs inside, if any. The block's
        exception, if it raises, fails the step and is raised again.
        """
        if parent_id is _UNSET:
            outer = context.current_step.get()
            parent_id = outer.id if outer is not None and outer.run is self else None
        step = self._start_step(node, page, attempt, input, parent_id)
        token = context.current_step.set(step)
        try:
            try:
                yield step
            except BaseException as e:
                step._fail_with(e)
                self._end_step(step)
                raise
            self._end_step(step)
        finally:
            context.current_step.reset(token)

    def _start_step(self, node, page, attempt, input, parent_id) -> "Step":
        if self.ended:
            raise RunError(f"pipeline run {self.id} has already ended")
        params = {
            "run": self.id,
            "parent": parent_id,
            "node": node,
            "page": page,
            "attempt": attempt,
            "input": None if input is _UNSET else Jsonb(jsonable(input)),
        }
        with self._tx() as cur:
            # Lock the run so concurrent steps get distinct numbers, and so
            # no step starts on a run that another thread has just ended.
            cur.execute(
                "SELECT ended_at IS NULL FROM pipeline_runs WHERE id = %(run)s"
                " FOR UPDATE",
                params,
            )
            row = cur.fetchone()
            if row is None or not row[0]:
                raise RunError(f"pipeline run {self.id} has already ended")
            cur.execute(
                "INSERT INTO pipeline_steps"
                " (run_id, parent_step_id, seq, node, attempt, page, input)"
                " SELECT %(run)s, %(parent)s, coalesce(max(seq), 0) + 1, %(node)s,"
                "  coalesce(%(attempt)s, 1 + count(*) FILTER ("
                "   WHERE node = %(node)s AND page IS NOT DISTINCT FROM %(page)s)),"
                "  %(page)s, %(input)s"
                " FROM pipeline_steps WHERE run_id = %(run)s"
                " RETURNING id, seq, attempt",
                params,
            )
            step_id, seq, attempt = cur.fetchone()
        step = Step(self, str(step_id), seq, node, attempt, page, parent_id)
        logger.info(
            "step started: %s",
            node,
            extra={
                "obs": {
                    "event": "step_started",
                    "run_id": self.id,
                    "step_id": step.id,
                    "span_id": step.id,
                    "parent_span_id": parent_id,
                    "node": node,
                    "seq": seq,
                    "attempt": attempt,
                    "page": page,
                }
            },
        )
        return step

    def _end_step(self, step: "Step") -> None:
        status = FAILED if step.error is not None else OK
        params = {
            "run": self.id,
            "id": step.id,
            "status": status,
            "model": step.model,
            "role": step.model_role,
            "prompt_name": step.prompt_name,
            "prompt_hash": step.prompt_hash,
            "input_changed": step._input is not _UNSET,
            "input": None if step._input is _UNSET else Jsonb(jsonable(step._input)),
            "output": None if step._output is _UNSET else Jsonb(jsonable(step._output)),
            "prompt_tokens": step.prompt_tokens,
            "completion_tokens": step.completion_tokens,
            # Findings and errors can quote the document: identifiers are
            # masked here, on the telemetry row (the record keeps its own).
            "findings": Jsonb(
                [redact_finding(f, self.identifiers) for f in jsonable(step.findings)]
            ),
            "error": self.redacted(step.error),
        }
        with self._tx() as cur:
            if step.prompt_text is not None:
                # A prompt's text, the first time any step uses it.
                cur.execute(
                    "INSERT INTO prompts (hash, name, text) VALUES (%s, %s, %s)"
                    " ON CONFLICT (hash) DO NOTHING",
                    (step.prompt_hash, step.prompt_name, step.prompt_text),
                )
            cur.execute(
                "UPDATE pipeline_steps SET ended_at = now(), status = %(status)s,"
                " model = %(model)s, model_role = %(role)s,"
                " prompt_name = %(prompt_name)s, prompt_hash = %(prompt_hash)s,"
                " input = CASE WHEN %(input_changed)s THEN %(input)s::jsonb"
                "  ELSE input END,"
                " output = %(output)s, prompt_tokens = %(prompt_tokens)s,"
                " completion_tokens = %(completion_tokens)s,"
                " findings = %(findings)s, error = %(error)s"
                " WHERE id = %(id)s AND status = 'running'",
                params,
            )
            closed = cur.rowcount == 1
            # The step's call rows, in the same transaction as its totals.
            calls = step.model_call_list()
            if closed and calls:
                cur.executemany(
                    model_calls.INSERT,
                    [
                        model_calls.row(call, step_id=step.id, run_id=self.id, seq=n)
                        for n, call in enumerate(calls, start=1)
                    ],
                )
            if closed:
                cur.execute(
                    f"UPDATE pipeline_runs SET models_used = {_MODELS_USED}"
                    " WHERE id = %(run)s",
                    params,
                )
        step.status = status
        fields = {
            "event": "step_ended",
            "run_id": self.id,
            "step_id": step.id,
            "span_id": step.id,
            "parent_span_id": step.parent_id,
            "node": step.node,
            "seq": step.seq,
            "attempt": step.attempt,
            "page": step.page,
            "status": status,
            "duration_ms": _ms_since(step._started),
            "model": step.model,
            "model_role": step.model_role,
            "model_calls": step.model_calls,
            "prompt_name": step.prompt_name,
            "prompt_hash": step.prompt_hash,
            "prompt_tokens": step.prompt_tokens,
            "completion_tokens": step.completion_tokens,
            "findings": step.finding_counts(),
            "error_type": step.error_type,
            "reused_from_step": step.reused_from,
        }
        if not closed:
            # The run ended (and closed this step as failed) while it ran.
            logger.warning(
                "step %s had already been closed; its result was not stored",
                step.node,
                extra={"obs": {**fields, "event": "step_not_stored"}},
            )
            return
        level = logging.WARNING if status == FAILED else logging.INFO
        logger.log(level, "step ended: %s %s", step.node, status, extra={"obs": fields})


# -------------------------------------------------------------------
# Steps
# -------------------------------------------------------------------
class Step:
    """One node attempt while it runs. Get it inside a node with current_step()."""

    def __init__(self, run, step_id, seq, node, attempt, page, parent_id):
        self.run = run
        self.id = step_id  # the span id
        self.seq = seq
        self.node = node
        self.attempt = attempt
        self.page = page
        self.parent_id = None if parent_id is None else str(parent_id)
        self.status = RUNNING
        self.model: str | None = None
        self.model_role: str | None = None
        self.model_calls = 0
        self.prompt_name: str | None = None
        self.prompt_hash: str | None = None
        self.prompt_text: str | None = None  # stored in prompts when the step ends
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.findings: list = []
        self.error: str | None = None
        self.error_type: str | None = None
        self.reused_from: str | None = None
        self._input = _UNSET
        self._output = _UNSET
        self._model_complete = False
        self._calls: list[context.ModelCall] = []
        self._calls_lock = threading.Lock()  # a node may call models in threads
        self._started = time.monotonic()

    def set_prompt(
        self, name: str, text: str | None = None, *, hash: str | None = None
    ):
        """
        The prompt this step used: its name, and its text or a ready hash.
        A text is stored in the prompts table (once per hash) when the
        step ends, so the step can be replayed; a bare hash is not.
        """
        if (text is None) == (hash is None):
            raise ValueError("give the prompt's text or its hash, not both")
        self.prompt_name = name
        self.prompt_hash = hash if hash is not None else prompt_hash(text)
        self.prompt_text = text

    def set_input(self, value) -> None:
        """Replace the input recorded when the step started."""
        self._input = value

    def set_output(self, value) -> None:
        self._output = value

    @property
    def has_output(self) -> bool:
        return self._output is not _UNSET

    def reuse(self, step_id: str, *, model: str, role: str) -> None:
        """
        The step's output is an earlier step's, used again without a model
        call: the step names that step's model, and stores no tokens.
        """
        self.model = model
        self.model_role = role
        self.reused_from = step_id

    def add_findings(self, findings) -> None:
        """Validator findings (Finding models or dicts), stored with the step."""
        self.findings.extend(findings)

    def finding_counts(self) -> dict[str, int]:
        """Findings per severity: what the logs may say about them."""
        counts = Counter()
        for f in self.findings:
            severity = f.get("severity") if isinstance(f, dict) else f.severity
            counts[str(jsonable(severity))] += 1
        return dict(counts)

    def fail(self, error: str) -> None:
        """
        Mark the step failed without raising, e.g. when a parse reply doesn't
        fit the schema and the graph goes on to repair. `error` is stored in
        the database and never logged.
        """
        self.error = error
        self.error_type = None  # not an exception

    def _fail_with(self, exc: BaseException) -> None:
        self.error = _error_text(exc)
        self.error_type = type(exc).__name__

    def add_model_call(self, call: context.ModelCall) -> None:
        """
        Called by llm.chat for every model round trip, failed ones too.
        The call is queued for its pipeline_model_calls row (numbered in
        the order reported), and token counts add up over the step's calls.
        The step's model is the one whose complete reply came last (or the
        last one that replied, if none completed); a call that raised
        doesn't change it.
        """
        with self._calls_lock:
            self._calls.append(call)
            self._add_to_totals(call)

    def model_call_list(self) -> list:
        """The calls reported so far, in order."""
        with self._calls_lock:
            return list(self._calls)

    def _add_to_totals(self, call) -> None:
        self.model_calls += 1
        for name in ("prompt_tokens", "completion_tokens"):
            n = getattr(call, name)
            if n is not None:
                setattr(self, name, (getattr(self, name) or 0) + n)
        if call.status == "failed":
            return
        if call.complete or not self._model_complete:
            self.model = call.model
            self.model_role = call.role
            self._model_complete = self._model_complete or call.complete

    def provenance(self, **fields) -> Provenance:
        """
        Provenance pointing at this step, for the items it produced. Call
        it after the model call, so the model is known.
        """
        return Provenance(
            run_id=self.run.id,
            node=self.node,
            step_id=self.id,
            model=self.model,
            prompt_name=self.prompt_name,
            prompt_hash=self.prompt_hash,
            **fields,
        )


# -------------------------------------------------------------------
# The node wrapper
# -------------------------------------------------------------------
def current_run() -> Run | None:
    return context.current_run.get()


def add_identifiers(*values) -> None:
    """Note patient identifiers on the current run, if there is one."""
    step = context.current_step.get()
    run = step.run if step is not None else context.current_run.get()
    if run is not None:
        run.add_identifiers(*values)


def current_step() -> Step | None:
    return context.current_step.get()


def traced(node: str, *, input_of=None, output_of=None, page=None, attempt=None):
    """
    Decorator for a pipeline node `fn(state, ...)`: every call becomes a
    step of the current run. It raises RunError outside a run, so a node
    can't run unrecorded.

    - input_of(state): what to store as the step's input (default: the
      whole state). Leave out page images; bytes are stored as their size
      and hash anyway.
    - output_of(result): what to store as its output (default: the node's
      return value, unless the node called set_output itself).
    - page, attempt: a value, or a function of the state; by default no
      page and the next attempt of this node and page.
    """

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            run = context.current_run.get()
            if run is None:
                raise RunError(f"node {node!r} ran outside a pipeline run")
            state = args[0] if args else None
            with run.step(
                node,
                page=page(state) if callable(page) else page,
                attempt=attempt(state) if callable(attempt) else attempt,
                input=input_of(state) if input_of is not None else state,
            ) as step:
                result = fn(*args, **kwargs)
                if not step.has_output:
                    step.set_output(output_of(result) if output_of else result)
                return result

        wrapper.__medsum_node__ = node
        return wrapper

    return decorate


def is_traced(fn) -> bool:
    """Whether `fn` was wrapped by traced(); a test checks every graph node."""
    return getattr(fn, "__medsum_node__", None) is not None
