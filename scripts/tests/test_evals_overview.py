"""The overview eval: facts from the answer keys, code's scores, the judge."""

import json

import pytest

import llm
from dataset_records import key
from evals import history, progress
from evals import overview as ov
from evals.keys import documents, manifest

MODEL = llm.PRIMARY_MODEL
SHOWN = (
    "Stated diagnosis: Vitamin D deficiency (2025-03-15). "
    "Allergy recorded: Penicillin - hives (2025-03-15)."
)


def test_a_patients_inputs_are_their_keys_in_upload_order():
    inputs = ov.patient_inputs("fatima")
    entries = documents()
    ids = [d for d in manifest()["upload_order"] if entries[d]["patient"] == "fatima"]
    assert [i.document_id for i in inputs] == [f"doc-{d}" for d in ids]
    assert [i.status for i in inputs] == [entries[d]["expected_end_state"] for d in ids]
    assert inputs[0].record == key(ids[0])
    assert inputs == sorted(inputs, key=lambda i: i.uploaded_at)
    assert ov.patients() == [p["key"] for p in manifest()["patients"]]


@pytest.mark.parametrize(
    ("name", "text", "found"),
    [
        ("Haemoglobin", "haemoglobin is low", True),
        ("HDL", "HDL-C was low", True),
        ("HDL", "the HDLs", False),
        ("Blood Urea Nitrogen (BUN)", "BUN rose", True),
        ("Blood Urea Nitrogen (BUN)", "blood urea  nitrogen rose", True),
        ("Vitamin D (25-OH)", "Vitamin D deficiency", True),
        ("Uric Acid", "Uric", False),
    ],
)
def test_a_test_is_named_by_its_whole_name_or_its_short_form(name, text, found):
    assert ov.named(name, text) is found


def test_a_shown_overview_is_scored_on_the_tests_for_attention(model_client):
    model_client.replies[MODEL] = [SHOWN]
    data = ov.run_one("fatima", "Write the overview.")
    assert data["outcome"] == "shown" and data["text"] == SHOWN
    assert (data["attempts"], data["unknown_numbers"], data["unknown_words"]) == (
        1,
        0,
        0,
    )
    assert (data["attention_named"], data["attention"]) == (1, 1)


def test_an_overview_the_check_rejects_twice_is_not_shown(model_client):
    model_client.replies[MODEL] = ["Vitamin D is 99.9 and rising.", "Still 99.9."]
    data = ov.run_one("fatima", "Write the overview.")
    assert data["outcome"] == "rejected" and data["text"] is None
    assert data["attempts"] == 2
    assert data["unknown_numbers"] > 0


def test_no_model_answering_stops_the_run(model_client):
    model_client.replies[MODEL] = [ConnectionError("refused")]
    model_client.replies[llm.FALLBACK_MODEL] = [ConnectionError("refused")]
    with pytest.raises(progress.RunStopped):
        ov.run_one("fatima", "Write the overview.")


# -------------------------------------------------------------------
# A run, its replay and the judge
# -------------------------------------------------------------------
@pytest.fixture
def places(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(progress, "RESULTS_DIR", tmp_path / "results")
    return tmp_path


def args(places, mode, label):
    return [mode, "--model", MODEL, "--patients", "fatima",
            "--cassettes", str(places / "c"), "--out", str(places / label),
            "--label", label]  # fmt: skip


def report_of(path) -> dict:
    report = json.loads((path / "report.json").read_text(encoding="utf-8"))
    for key_ in ("generated_at", "label"):
        report.pop(key_)
    for row in report["models"].values():
        row.pop("seconds_per_overview")
        for u in row["units"]:
            u.pop("seconds")
    return report


def test_a_live_run_replays_and_writes_the_judge_packet(places, model_client):
    model_client.digests[MODEL] = "sha256:abc"
    model_client.digests[llm.FALLBACK_MODEL] = "sha256:def"
    model_client.replies[MODEL] = [SHOWN]
    assert ov.main(args(places, "--live", "live")) == 0
    assert (places / "c" / "overview" / "qwen3.5_4b" / "fatima.json").exists()
    calls = len(model_client.calls)
    assert ov.main(args(places, "--replay", "again")) == 0
    assert len(model_client.calls) == calls
    assert report_of(places / "live") == report_of(places / "again")

    packet = json.loads((places / "live" / "judge.json").read_text(encoding="utf-8"))
    (item,) = packet["items"]
    assert item["id"] == f"{MODEL}/fatima" and item["overview"] == SHOWN
    assert "Vitamin D deficiency" in item["facts"]
    assert "verdicts.json" in (places / "live" / "judge.md").read_text("utf-8")
    assert "Not judged yet" in (places / "live" / "report.md").read_text("utf-8")


def verdict(id_, supported=(True, True), misleading=False, omissions=()):
    return {
        "id": id_,
        "sentences": [{"text": "s", "supported": s, "why": ""} for s in supported],
        "omissions": list(omissions),
        "misleading": misleading,
    }


@pytest.fixture
def judged_run(places, model_client):
    model_client.digests[MODEL] = "sha256:abc"
    model_client.digests[llm.FALLBACK_MODEL] = "sha256:def"
    model_client.replies[MODEL] = [SHOWN]
    assert ov.main(args(places, "--live", "live")) == 0
    return places / "live"


def write_verdicts(places, data) -> str:
    path = places / "verdicts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_the_judges_verdicts_are_counted_in_the_report(judged_run, places):
    data = {"judge": "a review session", "verdicts": [
        verdict(f"{MODEL}/fatima", (True, False), omissions=["TSH trend"])
    ]}  # fmt: skip
    path = write_verdicts(places, data)
    assert ov.main(["--verdicts", path, "--report", str(judged_run)]) == 0
    report = json.loads((judged_run / "report.json").read_text(encoding="utf-8"))
    row = report["judge"]["models"][MODEL]
    assert (
        row["sentences_supported"]["hits"],
        row["sentences_supported"]["total"],
    ) == (
        1,
        2,
    )
    assert row["all_supported"]["hits"] == 0 and row["omissions"] == 1
    assert row["misleading"]["hits"] == 0
    md = (judged_run / "report.md").read_text(encoding="utf-8")
    assert "Judged by a review session" in md and "Sentences supported" in md


@pytest.mark.parametrize(
    ("verdicts", "why"),
    [
        ({"verdicts": [verdict(f"{MODEL}/fatima")]}, "who judged"),
        ({"judge": "j", "verdicts": []}, "not judged"),
        ({"judge": "j", "verdicts": [verdict(f"{MODEL}/fatima"),
                                     verdict(f"{MODEL}/fatima")]}, "twice"),
        ({"judge": "j", "verdicts": [verdict(f"{MODEL}/fatima"),
                                     verdict("other/x")]}, "not an item"),
        ({"judge": "j", "verdicts": [verdict(f"{MODEL}/fatima", ())]}, "no sentences"),
        ({"judge": "j", "verdicts": [{**verdict(f"{MODEL}/fatima"),
                                      "misleading": "no"}]}, "misleading"),
    ],
)  # fmt: skip
def test_verdicts_that_dont_answer_the_packet_are_refused(
    judged_run, places, capsys, verdicts, why
):
    before = (judged_run / "report.json").read_text(encoding="utf-8")
    path = write_verdicts(places, verdicts)
    assert ov.main(["--verdicts", path, "--report", str(judged_run)]) == 2
    assert why in capsys.readouterr().err
    assert (judged_run / "report.json").read_text(encoding="utf-8") == before
