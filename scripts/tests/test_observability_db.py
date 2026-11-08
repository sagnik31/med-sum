"""
Observability against the throwaway database: what a run and its steps
write to pipeline_runs and pipeline_steps, provenance checks, and that the
logs never carry what the database holds.
"""

import contextvars
import io
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest

import llm
from dataset_records import DOC_IDS, key
from db_rows import add_account
from observability import (
    JsonFormatter,
    RunError,
    check_provenance,
    current_step,
    prompt_hash,
    resume_run,
    stamp,
    start_run,
    traced,
)
from observability.tracing import LEFT_RUNNING_AT_END, LEFT_RUNNING_AT_RESUME
from validators import Finding, Severity, Validator

GRAPH_VERSION = "test-1"
MESSAGES = [{"role": "user", "content": "Parse this page."}]
SECRET = "Asha Rao, Hb 13.2"  # stands in for patient content


def add_document(db) -> str:
    """A document row to hang runs on."""
    user_id = add_account(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (user_id, original_name, content_type, storage_path)"
            " VALUES (%s, 'report.pdf', 'application/pdf', '/tmp/x.pdf') RETURNING id",
            (user_id,),
        )
        return str(cur.fetchone()[0])


@pytest.fixture
def document_id(db) -> str:
    return add_document(db)


def run_row(db, run_id) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT trace_id, document_id::text, graph_version, end_state,"
            " ended_at IS NOT NULL AS ended, models_used, error"
            " FROM pipeline_runs WHERE id = %s",
            (run_id,),
        )
        names = [c.name for c in cur.description]
        return dict(zip(names, cur.fetchone()))


def step_rows(db, run_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id::text, parent_step_id::text AS parent, seq, node, attempt,"
            " page, status, ended_at IS NOT NULL AS ended, model, model_role,"
            " prompt_name, prompt_hash, input, output, prompt_tokens,"
            " completion_tokens, findings, error"
            " FROM pipeline_steps WHERE run_id = %s ORDER BY seq",
            (run_id,),
        )
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


# -------------------------------------------------------------------
# Runs
# -------------------------------------------------------------------
def test_a_run_writes_its_row_and_its_end_state(db, document_id):
    with start_run(document_id, GRAPH_VERSION) as run:
        assert run_row(db, run.id)["ended"] is False
        run.finish("verified")

    row = run_row(db, run.id)
    assert row["document_id"] == document_id
    assert row["graph_version"] == GRAPH_VERSION
    assert row["end_state"] == "verified" and row["ended"]
    assert len(row["trace_id"]) == 32 and int(row["trace_id"], 16) >= 0
    assert run.conn.closed


def test_leaving_a_run_without_an_end_state_fails_it(db, document_id):
    with start_run(document_id, GRAPH_VERSION) as run:
        pass

    row = run_row(db, run.id)
    assert row["end_state"] == "failed"
    assert row["error"] == "the run ended without an end state"


def test_an_exception_fails_the_run_and_is_raised_again(db, document_id):
    with pytest.raises(KeyError):
        with start_run(document_id, GRAPH_VERSION) as run:
            raise KeyError("pages")

    row = run_row(db, run.id)
    assert row["end_state"] == "failed"
    assert row["error"] == "KeyError: 'pages'"


def test_a_run_ends_once_with_a_known_end_state(document_id):
    with start_run(document_id, GRAPH_VERSION) as run:
        with pytest.raises(ValueError):
            run.finish("completed")
        run.finish("needs_review")
        with pytest.raises(RunError):
            run.finish("verified")
        with pytest.raises(RunError):
            with run.step("parse"):
                pass


# -------------------------------------------------------------------
# Steps through the node wrapper
# -------------------------------------------------------------------
def test_a_traced_node_writes_a_complete_step(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = ['{"observations": []}']
    finding = Finding(
        validator=Validator.SOURCE_TEXT,
        severity=Severity.ERROR,
        message="13.2 is not on line 4",
        item_id="obs-1",
        lines=[4],
    )

    @traced("parse", input_of=lambda s: {"text": s["text"]}, page=lambda s: s["page"])
    def parse(state):
        step = current_step()
        step.set_prompt("txt/parse.txt", "Parse the page.")
        reply = llm.chat(MESSAGES, max_tokens=100)
        step.add_findings([finding])
        return {"parsed": json.loads(reply)}

    with start_run(document_id, GRAPH_VERSION) as run:
        result = parse({"text": "1| Hb 13.2", "page": 2, "image": b"png"})
        run.finish("needs_review")

    assert result == {"parsed": {"observations": []}}
    (step,) = step_rows(db, run.id)
    assert step["seq"] == 1 and step["attempt"] == 1 and step["page"] == 2
    assert step["node"] == "parse" and step["parent"] is None
    assert step["status"] == "ok" and step["ended"] and step["error"] is None
    assert step["input"] == {"text": "1| Hb 13.2"}
    assert step["output"] == {"parsed": {"observations": []}}
    assert step["prompt_name"] == "txt/parse.txt"
    assert step["prompt_hash"] == prompt_hash("Parse the page.")
    assert (step["model"], step["model_role"]) == (llm.PRIMARY_MODEL, "primary")
    assert step["findings"] == [finding.model_dump(mode="json")]
    assert run_row(db, run.id)["models_used"] == [llm.PRIMARY_MODEL]


def test_the_whole_state_is_the_default_input(db, document_id):
    @traced("classify")
    def classify(state):
        return {"type": "lab_report"}

    with start_run(document_id, GRAPH_VERSION) as run:
        classify({"document_id": document_id, "image": b"\x89PNG"})
        run.finish("verified")

    (step,) = step_rows(db, run.id)
    assert step["input"]["document_id"] == document_id
    assert step["input"]["image"]["$bytes"] == 4


def test_a_node_can_store_its_own_output_and_replace_its_input(db, document_id):
    @traced("transcribe", output_of=lambda r: "unused")
    def transcribe(state):
        current_step().set_input({"page": 1})
        current_step().set_output({"lines": 3})
        return {"pages": ["big"]}

    with start_run(document_id, GRAPH_VERSION) as run:
        transcribe({"everything": "x"})
        run.finish("verified")

    (step,) = step_rows(db, run.id)
    assert step["input"] == {"page": 1}
    assert step["output"] == {"lines": 3}


def test_attempts_count_per_node_and_page_and_seq_runs_across_the_run(db, document_id):
    @traced("transcribe", page=lambda s: s["page"])
    def transcribe(state):
        return {}

    @traced("parse")
    def parse(state):
        return {}

    with start_run(document_id, GRAPH_VERSION) as run:
        for page in (1, 2, 1):
            transcribe({"page": page})
        parse({})
        parse({})
        with run.step("parse", attempt=7):
            pass
        run.finish("verified")

    rows = [
        (s["seq"], s["node"], s["page"], s["attempt"]) for s in step_rows(db, run.id)
    ]
    assert rows == [
        (1, "transcribe", 1, 1),
        (2, "transcribe", 2, 1),
        (3, "transcribe", 1, 2),
        (4, "parse", None, 1),
        (5, "parse", None, 2),
        (6, "parse", None, 7),
    ]


def test_a_node_that_raises_fails_its_step_and_the_error_propagates(db, document_id):
    @traced("parse")
    def parse(state):
        current_step().add_findings(
            [Finding(validator=Validator.DATES, severity=Severity.FLAG, message="x")]
        )
        raise ValueError("reply is not JSON")

    with pytest.raises(ValueError):
        with start_run(document_id, GRAPH_VERSION) as run:
            parse({})

    (step,) = step_rows(db, run.id)
    assert step["status"] == "failed" and step["ended"]
    assert step["error"] == "ValueError: reply is not JSON"
    assert step["output"] is None
    assert len(step["findings"]) == 1  # recorded before the failure
    assert run_row(db, run.id)["error"] == "ValueError: reply is not JSON"


def test_a_node_can_fail_its_step_without_raising(db, document_id):
    @traced("parse")
    def parse(state):
        current_step().fail("2 schema errors")
        return {"route": "repair"}

    with start_run(document_id, GRAPH_VERSION) as run:
        assert parse({}) == {"route": "repair"}
        run.finish("needs_review")

    (step,) = step_rows(db, run.id)
    assert (step["status"], step["error"]) == ("failed", "2 schema errors")
    assert step["output"] == {"route": "repair"}


def test_a_step_inside_a_step_records_its_parent(db, document_id):
    @traced("validate")
    def validate(state):
        return {}

    @traced("parse")
    def parse(state):
        validate(state)
        return {}

    with start_run(document_id, GRAPH_VERSION) as run:
        parse({})
        validate({})
        run.finish("verified")

    parse_step, inner, outer = step_rows(db, run.id)
    assert inner["parent"] == parse_step["id"]
    assert outer["parent"] is None


def test_the_fallback_model_is_recorded_with_its_role(db, document_id, model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [("cut off", "length")]
    model_client.replies[llm.FALLBACK_MODEL] = ["whole reply"]

    @traced("summarise")
    def summarise(state):
        return {"text": llm.chat(MESSAGES, max_tokens=10)}

    with start_run(document_id, GRAPH_VERSION) as run:
        summarise({})
        run.finish("verified")

    (step,) = step_rows(db, run.id)
    assert (step["model"], step["model_role"]) == (llm.FALLBACK_MODEL, "fallback")
    assert run_row(db, run.id)["models_used"] == [llm.FALLBACK_MODEL]


def test_models_used_lists_each_model_once_in_order_of_first_use(
    db, document_id, model_client
):
    model_client.replies[llm.PRIMARY_MODEL] = ["a", ConnectionError("down"), "c"]
    model_client.replies[llm.FALLBACK_MODEL] = ["b"]

    @traced("parse")
    def parse(state):
        return {"text": llm.chat(MESSAGES, max_tokens=10)}

    with start_run(document_id, GRAPH_VERSION) as run:
        for _ in range(3):
            parse({})
        assert run_row(db, run.id)["models_used"] == [
            llm.PRIMARY_MODEL,
            llm.FALLBACK_MODEL,
        ]
        run.finish("verified")


def test_values_postgres_would_reject_are_stored(db, document_id):
    @traced("transcribe")
    def transcribe(state):
        return {"text": "line\x00one", "score": float("nan")}

    with start_run(document_id, GRAPH_VERSION) as run:
        transcribe({})
        run.finish("verified")

    (step,) = step_rows(db, run.id)
    assert step["output"] == {"text": "line�one", "score": "nan"}


def test_parallel_nodes_get_distinct_sequence_numbers(db, document_id):
    @traced("transcribe", page=lambda s: s["page"])
    def transcribe(state):
        return {}

    with start_run(document_id, GRAPH_VERSION) as run:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, transcribe, {"page": p})
                for p in range(1, 13)
            ]
            for f in futures:
                f.result()
        run.finish("verified")

    rows = step_rows(db, run.id)
    assert [s["seq"] for s in rows] == list(range(1, 13))
    assert sorted(s["page"] for s in rows) == list(range(1, 13))
    assert all(s["status"] == "ok" for s in rows)


# -------------------------------------------------------------------
# Ending and resuming with steps open
# -------------------------------------------------------------------
def test_finishing_closes_steps_still_running(db, document_id):
    with start_run(document_id, GRAPH_VERSION) as run:
        # As if a thread were still in the node when the run ended.
        orphan = run._start_step("transcribe", 1, None, {"page": 1}, None)
        run.finish("failed")
        run._end_step(orphan)  # too late: not stored, no error

    (step,) = step_rows(db, run.id)
    assert (step["status"], step["error"]) == ("failed", LEFT_RUNNING_AT_END)
    assert step["output"] is None


def test_a_resumed_run_closes_interrupted_steps_and_continues_the_sequence(
    db, database_url, document_id
):
    @traced("transcribe", page=lambda s: s["page"])
    def transcribe(state):
        return {}

    crashed = start_run(document_id, GRAPH_VERSION)
    with crashed:
        transcribe({"page": 1})
        crashed._start_step("transcribe", 2, None, None, None)
        crashed.ended = True  # the process died: nothing more is written

    with resume_run(crashed.id) as run:
        assert run.trace_id == crashed.trace_id
        transcribe({"page": 2})
        run.finish("verified")

    rows = [
        (s["seq"], s["page"], s["attempt"], s["status"], s["error"])
        for s in step_rows(db, run.id)
    ]
    assert rows == [
        (1, 1, 1, "ok", None),
        (2, 2, 1, "failed", LEFT_RUNNING_AT_RESUME),
        (3, 2, 2, "ok", None),
    ]
    assert run_row(db, run.id)["end_state"] == "verified"


def test_only_an_open_run_can_be_resumed(document_id):
    with start_run(document_id, GRAPH_VERSION) as run:
        run.finish("verified")

    with pytest.raises(RunError, match="already ended"):
        resume_run(run.id)
    with pytest.raises(RunError, match="no pipeline run"):
        resume_run(str(uuid.uuid4()))


def test_a_run_can_use_a_given_connection_factory(db, database_url, document_id):
    opened = []

    def connect():
        conn = psycopg2.connect(database_url)
        opened.append(conn)
        return conn

    with start_run(document_id, GRAPH_VERSION, connect=connect) as run:
        run.finish("verified")

    assert len(opened) == 1 and opened[0].closed


# -------------------------------------------------------------------
# Provenance
# -------------------------------------------------------------------
def test_items_stamped_by_a_step_trace_back_to_it(db, document_id):
    record = key(DOC_IDS[0])

    @traced("parse")
    def parse(state):
        current_step().set_prompt("txt/parse.txt", "Parse.")
        stamp(record, current_step().provenance())
        return {}

    with start_run(document_id, GRAPH_VERSION) as run:
        parse({})
        run.finish("verified")

    (step,) = step_rows(db, run.id)
    first = next(record.items())
    assert first.provenance.step_id == step["id"]
    assert first.provenance.run_id == run.id
    assert first.provenance.prompt_hash == step["prompt_hash"]
    assert check_provenance(db, record, run_id=run.id) == []


def test_check_provenance_names_each_broken_link(db, document_id):
    record = key(DOC_IDS[0])
    with start_run(document_id, GRAPH_VERSION) as run:
        with run.step("parse") as step:
            stamp(record, step.provenance())
        run.finish("verified")

    items = list(record.items())
    items[0].provenance = None
    items[1].provenance = items[1].provenance.model_copy(update={"step_id": None})
    items[2].provenance = items[2].provenance.model_copy(update={"node": "repair"})
    items[3].provenance = items[3].provenance.model_copy(
        update={"step_id": str(uuid.uuid4())}
    )
    items[4].provenance = items[4].provenance.model_copy(update={"step_id": "s-1"})

    problems = check_provenance(db, record)
    assert len(problems) == 5
    assert "no provenance" in problems[0]
    assert "no step id" in problems[1]
    assert "is not a uuid" in problems[2]
    # Then the links that reached the database, in item order.
    assert "a parse step, not repair" in problems[3]
    assert "is not in pipeline_steps" in problems[4]

    other_run = str(uuid.uuid4())
    assert any(
        f"not {other_run}" in p for p in check_provenance(db, record, run_id=other_run)
    )


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


def test_logs_carry_ids_and_timings_but_never_patient_content(
    db, document_id, model_client, json_log
):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = [SECRET]

    @traced("parse", input_of=lambda s: s)
    def parse(state):
        current_step().set_prompt("txt/parse.txt", SECRET)
        llm.chat(MESSAGES, max_tokens=10)
        current_step().add_findings(
            [
                Finding(
                    validator=Validator.SOURCE_TEXT,
                    severity=Severity.ERROR,
                    message=f"{SECRET} is not on line 4",
                )
            ]
        )
        return {"out": SECRET}

    @traced("repair")
    def repair(state):
        raise ValueError(f"cannot repair {SECRET}")

    with pytest.raises(ValueError):
        with start_run(document_id, GRAPH_VERSION) as run:
            parse({"text": SECRET})
            repair({"text": SECRET})

    log = json_log.getvalue()
    assert SECRET not in log and "Asha" not in log
    lines = [json.loads(s) for s in log.splitlines()]
    events = {line.get("event"): line for line in lines if line.get("event")}
    assert set(events) >= {"run_started", "step_started", "step_ended", "run_ended"}

    parse_end = next(
        e for e in lines if e.get("event") == "step_ended" and e["node"] == "parse"
    )
    assert parse_end["run_id"] == run.id
    assert parse_end["trace_id"] == run.trace_id
    assert parse_end["document_id"] == document_id
    assert parse_end["status"] == "ok"
    assert parse_end["model_role"] == "fallback"
    assert parse_end["findings"] == {"error": 1}
    assert isinstance(parse_end["duration_ms"], int)

    repair_end = next(
        e for e in lines if e.get("event") == "step_ended" and e["node"] == "repair"
    )
    assert (repair_end["status"], repair_end["error_type"]) == ("failed", "ValueError")
    assert events["run_ended"]["end_state"] == "failed"

    # The primary's failure, logged by llm.py inside the step, has its ids.
    warning = next(e for e in lines if e["logger"] == "llm")
    assert warning["step_id"] == parse_end["step_id"]

    # The content is all in the database.
    parse_row, repair_row = step_rows(db, run.id)
    assert parse_row["input"] == {"text": SECRET}
    assert parse_row["output"] == {"out": SECRET}
    assert SECRET in parse_row["findings"][0]["message"]
    assert SECRET in repair_row["error"]
