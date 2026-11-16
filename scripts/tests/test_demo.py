"""
The cassette demo (python -m demo): with cassettes recorded over the fake
model, it runs both documents through the real graph in a throwaway
database of its own and prints the trace, findings, signals and end
state; without them it says what is missing and how to record it, and
starts nothing.
"""

import json

import pytest

import demo
import llm
from conftest import ADMIN_URL
from dataset_records import key, parse_of
from db_rows import add_account
from demo.__main__ import main
from evals import run as runner
from evals.cassettes import Cassette, RecordingClient

MODEL = llm.PRIMARY_MODEL


def _replies(doc_id: str, doc_type: str) -> list[str]:
    pages = ["\n".join(p.lines) for p in key(doc_id).pages]
    return [*pages, json.dumps({"document_type": doc_type}),
            parse_of(key(doc_id)).model_dump_json()]  # fmt: skip


@pytest.fixture
def cassettes(db, model_client, tmp_path):
    """d09 and d15 recorded through the graph over the fake model."""
    user = add_account(db)
    for doc_id, doc_type in (("d09", "lab_report"), ("d15", "prescription")):
        model_client.replies[MODEL] = _replies(doc_id, doc_type)
        cassette = Cassette()
        runner.run_model(MODEL, [doc_id], user_id=user,
                         client_for=lambda d: RecordingClient(model_client, cassette))  # fmt: skip
        cassette.save(runner.cassette_path(MODEL, doc_id, tmp_path))
    return tmp_path


def _run(cassettes, **kw) -> tuple[int, str]:
    printed: list[str] = []
    code = demo.run(MODEL, cassettes=cassettes, out=printed.append, **kw)
    return code, "\n".join(printed)


def test_the_demo_runs_both_documents_from_cassettes(cassettes, model_client):
    calls = len(model_client.calls)
    code, text = _run(cassettes, database=lambda: runner.EvalDatabase(
        admin_url=ADMIN_URL, name="med_sum_test_demo"))  # fmt: skip
    assert code == 0, text
    assert len(model_client.calls) == calls  # no model was called
    assert "== d09: lab_report" in text and "== d15: prescription" in text
    assert "End state: verified (the dataset expects verified)" in text
    assert "End state: needs_review (the dataset expects needs_review)" in text
    # d15 has no date: its signal and plain reason are printed.
    assert "LC-12:" in text
    # The trace shows the stored steps, with the model and its role.
    for node in ("transcribe p1", "classify", "parse", "validate", "finish"):
        assert node in text
    assert f"{MODEL} (primary)" in text
    assert "field F1 1.000, 0 unsafe error(s)" in text
    assert "the throwaway database is dropped" in text


def test_the_demo_database_is_dropped(cassettes, db):
    _run(cassettes, database=lambda: runner.EvalDatabase(
        admin_url=ADMIN_URL, name="med_sum_test_demo"))  # fmt: skip
    import psycopg

    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'med_sum_test_demo'"
        ).fetchone()
    assert row is None


def test_the_demo_never_uses_med_sum():
    assert demo.DB_NAME != "med_sum"
    with pytest.raises(ValueError):
        runner.EvalDatabase(admin_url=ADMIN_URL, name="med_sum")


def test_missing_cassettes_are_named_with_how_to_record_them(tmp_path):
    def no_database():
        raise AssertionError("no database is made when a cassette is missing")

    code, text = _run(tmp_path, database=no_database)
    assert code == 2
    assert str(runner.cassette_path(MODEL, "d09", tmp_path)) in text
    assert str(runner.cassette_path(MODEL, "d15", tmp_path)) in text
    assert f"python -m evals.run --live --model {MODEL} --docs d09,d15" in text


def test_a_cassette_that_no_longer_matches_stops_the_demo(cassettes, monkeypatch):
    # A changed transcription prompt makes every recorded request a miss.
    from pipeline import nodes

    original = nodes.transcription_messages

    def changed(*a, **kw):
        messages = original(*a, **kw)
        messages[0] = {**messages[0], "content": messages[0]["content"] + " changed"}
        return messages

    monkeypatch.setattr(nodes, "transcription_messages", changed)
    code, text = _run(cassettes, database=lambda: runner.EvalDatabase(
        admin_url=ADMIN_URL, name="med_sum_test_demo"))  # fmt: skip
    assert code == 2
    assert "no longer matches" in text


def test_the_command_line_refuses_unknown_documents(tmp_path):
    with pytest.raises(SystemExit):
        main(["--docs", "d99", "--cassettes", str(tmp_path)])


def test_the_command_line_reports_missing_cassettes(tmp_path, capsys):
    assert main(["--cassettes", str(tmp_path), "--model", MODEL]) == 2
    assert "missing" in capsys.readouterr().out
