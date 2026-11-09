"""Choosing the primary model from eval summaries: safety gate, then F1, then speed."""

from evals import Counts, Ratio, choose_primary
from evals.choose import MAX_UNSAFE_RATE, MIN_VALUE_RECALL
from evals.choose import Candidate as RealCandidate
from evals.score import Summary
from evals.transcription import TranscriptionSummary

PASSING = object()


def transcription(correct: int, total: int = 100) -> TranscriptionSummary:
    return TranscriptionSummary(documents=1, values=Ratio(correct, total))


def Candidate(model, summary, transcription_=PASSING, **kw):
    """A candidate whose transcription passes the gate unless one is given."""
    if transcription_ is PASSING:
        transcription_ = transcription(100)
    return RealCandidate(model, summary, transcription_, **kw)


def summary(f1_tp: int, errors: int, unsafe: int = 0, items: int = 100) -> Summary:
    """A summary whose field F1 is tp / (tp + errors), split as FP and FN."""
    s = Summary(documents=1, unsafe=unsafe, clinical_items=items)
    s.fields["observations"] = Counts(tp=f1_tp, fp=errors, fn=errors)
    return s


def test_the_higher_f1_wins_when_both_pass_the_gate():
    choice = choose_primary(
        [Candidate("qwen", summary(90, 10)), Candidate("gemma", summary(80, 20))]
    )
    assert choice.model == "qwen" and choice.reason.startswith("higher field F1")
    assert choice.qualified == ("qwen", "gemma") and choice.excluded == {}


def test_a_model_over_the_unsafe_limit_is_excluded_whatever_its_f1():
    choice = choose_primary(
        [
            Candidate("qwen", summary(95, 5, unsafe=3)),
            Candidate("gemma", summary(70, 30, unsafe=1)),
        ]
    )
    assert choice.model == "gemma"
    assert "3 of 100" in choice.excluded["qwen"]


def test_the_limit_itself_passes():
    at_limit = round(MAX_UNSAFE_RATE * 100)
    choice = choose_primary([Candidate("qwen", summary(90, 10, unsafe=at_limit))])
    assert choice.model == "qwen"


def test_no_model_is_chosen_when_none_passes():
    choice = choose_primary(
        [
            Candidate("qwen", summary(90, 10, unsafe=5)),
            Candidate("gemma", summary(90, 10, unsafe=9)),
        ]
    )
    assert choice.model is None and set(choice.excluded) == {"qwen", "gemma"}


def test_a_model_with_nothing_scored_is_excluded():
    choice = choose_primary(
        [Candidate("qwen", Summary()), Candidate("gemma", summary(50, 50))]
    )
    assert choice.model == "gemma" and choice.excluded["qwen"] == "nothing was scored"


def test_speed_breaks_an_f1_tie():
    choice = choose_primary(
        [
            Candidate("qwen", summary(901, 99), seconds_per_document=300),
            Candidate("gemma", summary(900, 100), seconds_per_document=120),
        ]
    )
    assert choice.model == "gemma" and "faster" in choice.reason


def test_a_tie_without_timings_stays_with_the_higher_f1():
    choice = choose_primary(
        [Candidate("qwen", summary(900, 100)), Candidate("gemma", summary(901, 99))]
    )
    assert choice.model == "gemma" and "no timings" in choice.reason


# -------------------------------------------------------------------
# The transcription gate
# -------------------------------------------------------------------
def test_low_transcription_value_recall_excludes_a_model():
    choice = choose_primary(
        [
            Candidate("qwen", summary(95, 5), transcription(90)),
            Candidate("gemma", summary(80, 20), transcription(97)),
        ]
    )
    assert choice.model == "gemma"
    assert "90 of 100 values" in choice.excluded["qwen"]


def test_the_value_recall_minimum_itself_passes():
    at_minimum = round(MIN_VALUE_RECALL * 100)
    choice = choose_primary(
        [Candidate("qwen", summary(90, 10), transcription(at_minimum))]
    )
    assert choice.model == "qwen"


def test_a_model_without_transcription_scores_does_not_qualify():
    for missing in (None, TranscriptionSummary()):
        choice = choose_primary([Candidate("qwen", summary(90, 10), missing)])
        assert choice.model is None
        assert choice.excluded["qwen"] == "no transcription scores"


def test_the_unsafe_gate_is_checked_before_the_transcription_gate():
    choice = choose_primary(
        [Candidate("qwen", summary(90, 10, unsafe=5), transcription(50))]
    )
    assert choice.excluded["qwen"].startswith("unsafe-error rate")
