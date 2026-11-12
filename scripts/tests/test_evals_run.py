"""
The eval runner (evals/run.py): dataset documents through the real graph
with a recording client over the fake model, then the same run replayed
from the cassette, scored and routed.
"""

import json

import pytest

import llm
from dataset_records import key, parse_of
from db_rows import add_account
from evals import run as runner
from evals.cassettes import Cassette, CassetteMiss, RecordingClient, ReplayClient
from evals.keys import manifest
from evals.report import build_report
from pipeline import nodes

MODEL = llm.PRIMARY_MODEL


def replies_for(doc_id: str, doc_type: str) -> list[str]:
    pages = ["\n".join(p.lines) for p in key(doc_id).pages]
    return [
        *pages,
        json.dumps({"document_type": doc_type}),
        parse_of(key(doc_id)).model_dump_json(),
    ]


@pytest.fixture
def recorded(db, model_client):
    """d09 run once through the graph with its cassette recorded."""
    model_client.replies[MODEL] = replies_for("d09", "lab_report")
    cassette = Cassette()
    user = add_account(db)
    run, outcomes = runner.run_model(
        MODEL,
        ["d09"],
        user_id=user,
        client_for=lambda doc: RecordingClient(model_client, cassette),
    )
    return cassette, user, run, outcomes


def test_a_live_run_scores_and_routes_each_document(recorded):
    cassette, _, run, outcomes = recorded
    (result,) = run.documents
    assert result.record.all_fields.f1 == 1.0
    assert result.transcription.values.value == 1.0
    assert outcomes["d09"] == runner.Outcome("verified", [], "primary")
    # One page, classify, parse.
    assert len(cassette.entries) == 3


def test_a_replay_gives_the_same_result_without_the_model(recorded, model_client):
    cassette, user, run, outcomes = recorded
    calls = len(model_client.calls)
    again, again_outcomes = runner.run_model(
        MODEL, ["d09"], user_id=user, client_for=lambda doc: ReplayClient(cassette)
    )
    assert len(model_client.calls) == calls
    assert again_outcomes == outcomes
    assert again.documents[0].record == run.documents[0].record


def test_a_replay_after_a_prompt_change_fails_with_re_record(recorded, monkeypatch):
    cassette, user, _, _ = recorded
    real = nodes.prompt
    monkeypatch.setattr(
        nodes, "prompt", lambda name: real(name) + ("\nNew rule." if name == "parse_lab_report" else "")
    )  # fmt: skip
    with pytest.raises(CassetteMiss, match="the prompt changed"):
        runner.run_model(
            MODEL, ["d09"], user_id=user, client_for=lambda d: ReplayClient(cassette)
        )


def test_the_runner_puts_the_models_and_today_back(recorded):
    assert llm._MODELS == {"primary": llm.PRIMARY_MODEL, "fallback": llm.FALLBACK_MODEL}
    assert nodes.today is not runner.TODAY


def test_a_failed_run_counts_everything_as_missed(db, model_client):
    # The fake model has no replies, so the first call raises.
    run, outcomes = runner.run_model(
        MODEL, ["d02"], user_id=add_account(db), client_for=lambda d: model_client
    )
    (result,) = run.documents
    assert result.record.type_got is None
    assert outcomes["d02"].end_state == "failed"
    assert outcomes["d02"].error == "AssertionError"


def test_the_other_model_runs_as_primary_with_the_first_as_fallback(db, model_client):
    other = llm.FALLBACK_MODEL
    model_client.replies[other] = replies_for("d09", "lab_report")
    run, outcomes = runner.run_model(
        other, ["d09"], user_id=add_account(db), client_for=lambda d: model_client
    )
    assert {c["model"] for c in model_client.calls} == {other}
    assert outcomes["d09"].record_model_role == "primary"
    assert run.model == other


def test_the_report_has_the_risk_coverage_line(recorded):
    _, _, run, outcomes = recorded
    report = build_report(
        "test", [run], manifest(),
        outcomes={MODEL: {d: o.__dict__ for d, o in outcomes.items()}},
    )  # fmt: skip
    routing = report["models"][MODEL]["routing"]
    assert routing["auto_verified"]["hits"] == 1
    assert routing["end_state_right"] == {
        **routing["end_state_right"],
        "hits": 1,
        "total": 1,
    }
    assert report["models"][MODEL]["splits"]["dev"]["documents"] == 1


# -------------------------------------------------------------------
# Command line pieces
# -------------------------------------------------------------------
def test_select_filters_by_split_and_refuses_unknown_ids():
    assert runner.select(None, "dev") == ["d02", "d09"]
    assert runner.select("d09,d01", "dev") == ["d09"]
    with pytest.raises(SystemExit, match="d99"):
        runner.select("d99", None)


def test_cassette_paths_are_per_model_and_document(tmp_path):
    path = runner.cassette_path("qwen3.5:4b", "d01", tmp_path)
    assert path == tmp_path / "qwen3.5_4b" / "d01.json"


def test_the_eval_database_is_never_med_sum():
    with pytest.raises(ValueError):
        runner.EvalDatabase(name="med_sum")


def test_replay_without_a_cassette_exits_with_re_record(tmp_path, monkeypatch, capsys):
    class NoDatabase:
        user_id = "u"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(runner, "EvalDatabase", NoDatabase)
    code = runner.main(["--replay", "--docs", "d09", "--cassettes", str(tmp_path),
                        "--model", MODEL, "--out", str(tmp_path / "out")])  # fmt: skip
    assert code == 2
    assert "re-record" in capsys.readouterr().err
