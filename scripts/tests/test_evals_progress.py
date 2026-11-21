"""Checkpointed eval runs (evals/progress.py): the parts that need no run."""

import errno

import httpx
import psycopg
import pytest

import llm
from evals import progress


def run(**changes) -> dict:
    base = progress.fingerprint(
        kind="run", models=["m"], units=["m/d01", "m/d02"], dataset="h",
        digests={"m": "sha256:1"},
    )  # fmt: skip
    return {**base, **changes}


def test_a_started_run_is_refused_a_second_start_and_resumed_as_is(tmp_path):
    p = progress.Progress.open("x", run(), resume=False, root=tmp_path, out=print)
    p.save("m/d01", {"a": 1}, seconds=3, end="verified")
    with pytest.raises(progress.ResumeRefused, match="already has progress"):
        progress.Progress.open("x", run(), resume=False, root=tmp_path)
    again = progress.Progress.open("x", run(), resume=True, root=tmp_path)
    assert again.load("m/d01") == {"a": 1} and again.load("m/d02") is None
    assert again.finished() == ["m/d01"]
    again.finish()
    assert not (tmp_path / "x").exists()


def test_a_resume_names_what_changed(tmp_path):
    progress.Progress.open("x", run(), resume=False, root=tmp_path)
    changed = run(digests={"m": "sha256:2"}, units=["m/d01"])
    with pytest.raises(progress.ResumeRefused) as info:
        progress.Progress.open("x", changed, resume=True, root=tmp_path)
    assert "model digests" in str(info.value) and "units to run" in str(info.value)


def test_nothing_to_resume_is_refused(tmp_path):
    with pytest.raises(progress.ResumeRefused, match="no run named"):
        progress.Progress.open("nope", run(), resume=True, root=tmp_path)


def test_a_unit_file_is_written_whole(tmp_path):
    p = progress.Progress.open("x", run(), resume=False, root=tmp_path)
    p.save("m/d01", {"a": 1}, seconds=1, end=None)
    files = sorted(f.name for f in (p.directory / "units").iterdir())
    assert files == ["m_d01.json"]  # no temporary file left behind
    with pytest.raises(ValueError):
        p.save("m/d99", {}, seconds=1, end=None)


def test_the_progress_line_gives_the_time_left():
    line = progress.progress_line("m/d01", 600, "verified", 4, [600, 540])
    assert line == "m/d01: 600 s, verified; 4 units left, about 38 min left"
    assert "left," not in progress.progress_line("m/d02", 5, None, 0, [5])


@pytest.mark.parametrize(
    "error, stops",
    [
        (llm.ModelUnavailableError("off"), True),
        (llm.ModelsDownError("down"), True),
        (psycopg.OperationalError("connection lost"), True),
        (OSError(errno.ENOSPC, "No space left on device"), True),
        (llm.ModelOutputError("bad output"), False),
        (llm.OutputCappedError("capped"), False),
        (ValueError("a bad parse"), False),
        (httpx.ReadTimeout("slow"), False),  # only through llm, as ModelsDownError
    ],
)
def test_infrastructure_errors_are_told_from_document_errors(error, stops):
    assert progress.is_infrastructure(error) is stops


def test_an_infrastructure_cause_is_found_behind_a_wrapper():
    try:
        try:
            raise llm.ModelsDownError("down")
        except llm.ModelsDownError as e:
            raise RuntimeError("node failed") from e
    except RuntimeError as wrapped:
        with pytest.raises(progress.RunStopped):
            progress.stop_if_infrastructure(wrapped)


def test_the_code_fingerprint_ignores_eval_outputs(monkeypatch):
    status = " M scripts/src/llm.py\n?? scripts/evals/cassettes/m/d01.json\n"
    outputs_only = (
        "?? scripts/evals/cassettes/m/d01.json\n?? scripts/evals/reports/x/\n"
    )

    def git(status_text):
        def fake(*args):
            if args[0] == "status":
                return status_text
            return "abc\n" if args[0] == "rev-parse" else ""

        return fake

    monkeypatch.setattr(progress, "_git", git(outputs_only))
    clean = progress.code_fingerprint()
    assert clean["dirty"] is False
    monkeypatch.setattr(progress, "_git", git(""))
    assert progress.code_fingerprint() == clean
    monkeypatch.setattr(progress, "_git", git(status))
    assert progress.code_fingerprint()["dirty"] is True


def test_both_models_unreachable_raise_models_down(model_client):
    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = [httpx.ConnectError("refused")]
    with pytest.raises(llm.ModelsDownError):
        llm.chat([{"role": "user", "content": "x"}], max_tokens=5)


def test_a_bad_request_on_one_model_is_not_an_outage(model_client):
    import ollama

    model_client.replies[llm.PRIMARY_MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = [ollama.ResponseError("bad", 400)]
    with pytest.raises(llm.ModelOutputError) as info:
        llm.chat([{"role": "user", "content": "x"}], max_tokens=5)
    assert not isinstance(info.value, llm.ModelsDownError)
