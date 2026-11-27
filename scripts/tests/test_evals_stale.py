"""
Recordings known to be stale (evals/stale.py): left out of a replay by name,
and the list can neither hide a new one nor keep one that replays.
"""

import json

import pytest

import llm
from dataset_records import key
from evals import compare, history, injection, selfcheck, stale
from evals import run as runner
from evals.cassettes import CassetteMiss

MODEL = llm.PRIMARY_MODEL


def test_the_committed_list_names_real_units():
    known = {"e2e": runner.select(None, None), "injection": injection.select(None)}
    for kind, ids in known.items():
        for model, units in stale.load(kind).items():
            assert model in (llm.PRIMARY_MODEL, llm.FALLBACK_MODEL)
            assert len(set(units)) == len(units) and set(units) < set(ids)


def test_a_unit_that_replays_is_no_longer_stale():
    def replay(unit):
        if unit == "d01":
            raise CassetteMiss("changed")

    assert stale.replaying(["d01", "d02"], replay) == ["d02"]


def test_the_notice_names_what_was_not_measured(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert stale.notice("e2e", {"m": []}) == ""
    text = stale.notice("e2e", {"m": ["d01", "d02"], "n": []})
    assert "2 e2e units NOT MEASURED" in text and "m: d01, d02" in text
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert stale.notice("e2e", {"m": ["d01"]}).startswith("::warning::")


class NoDatabase:
    user_id = "u"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@pytest.fixture
def replay(tmp_path, monkeypatch):
    """main() replaying d09 and d11 with d09 listed; `missing` are the units
    with no matching recording."""
    missing = {"d09"}
    path = tmp_path / "stale.json"
    path.write_text(json.dumps({"e2e": {MODEL: ["d09"]}}), encoding="utf-8")
    load = stale.load
    monkeypatch.setattr(stale, "load", lambda kind: load(kind, path))
    ends = {d: runner.Outcome(**o) for d, o in selfcheck.selfcheck_outcomes().items()}

    def run_model(model, ids, *, on_unit=lambda *a: None, **_):
        for d in ids:
            if d in missing:
                raise CassetteMiss(f"{d} changed")
            on_unit(d, {"record": key(d)}, ends[d], 1.0)

    monkeypatch.setattr(runner, "EvalDatabase", NoDatabase)
    monkeypatch.setattr(runner, "run_model", run_model)
    monkeypatch.setattr(runner, "replay_digests", lambda *a: {})

    def go(*more):
        return runner.main(["--replay", "--docs", "d09,d11", "--model", MODEL,
                            "--label", tmp_path.name, "--out", str(tmp_path / "o"),
                            *more])  # fmt: skip

    return go, missing, tmp_path / "o"


def test_a_replay_leaves_out_the_listed_unit_and_says_so(replay, capsys):
    go, _, out = replay
    assert go("--known-stale") == 0
    assert "NOT MEASURED" in capsys.readouterr().out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["not_measured"] == {MODEL: ["d09"]}
    assert history.document_ids(report) == {MODEL: ["d11"]}
    assert "Not measured" in (out / "report.md").read_text(encoding="utf-8")


def test_a_new_stale_unit_fails(replay, capsys):
    go, missing, _ = replay
    missing.add("d11")
    assert go("--known-stale") == 2
    assert "d11 changed" in capsys.readouterr().err


def test_a_listed_unit_that_replays_fails(replay, capsys):
    go, missing, _ = replay
    missing.clear()
    assert go("--known-stale") == 2
    assert "d09 replay again" in capsys.readouterr().err


def test_without_the_option_a_listed_unit_still_fails(replay):
    go, _, _ = replay
    assert go() == 2


@pytest.mark.parametrize("main", [runner.main, injection.main])
def test_a_live_run_never_leaves_units_out(main):
    with pytest.raises(SystemExit):
        main(["--live", "--known-stale"])


def committed():
    entry = history.read(history.latest("e2e"))
    path = history.REPORTS_DIR.parent / entry["report"] / "report.json"
    return entry, json.loads(path.read_text(encoding="utf-8"))


def test_the_numbers_over_every_document_are_the_report_s_own():
    entry, report = committed()
    for model, m in report["models"].items():
        got = history.restricted(m, entry["documents"][model])
        assert compare._values(got) == pytest.approx(
            compare._values(entry["models"][model])
        )


def test_the_baseline_is_compared_without_the_stale_documents(
    tmp_path, capsys, monkeypatch
):
    entry, report = committed()
    # Its own list, so the test holds when the committed one is empty.
    listed = {MODEL: entry["documents"][MODEL][:2]}
    monkeypatch.setattr(stale, "load", lambda kind: listed)
    b = {**entry, "documents": {}, "models": {}}
    for model, m in report["models"].items():
        b["documents"][model] = stale.without(entry["documents"][model], listed, model)
        b["models"][model] = history.restricted(m, b["documents"][model])
    path = tmp_path / "b.json"

    def run(*more):
        path.write_text(json.dumps(b), encoding="utf-8")
        return compare.main([str(history.latest("e2e")), str(path), *more])

    assert run("--known-stale") == 0
    assert "NOT MEASURED" in capsys.readouterr().out
    assert run() == 2  # every document, or the list: nothing between
    b["models"][MODEL]["unsafe"] += 1
    assert run("--known-stale") == 1
    b["documents"][MODEL] = b["documents"][MODEL][1:]
    assert run("--known-stale") == 2
