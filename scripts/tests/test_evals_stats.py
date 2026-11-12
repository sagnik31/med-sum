"""Wilson score intervals (evals/stats.py)."""

import pytest

from evals.stats import wilson


def test_zero_of_n_still_has_an_upper_bound():
    i = wilson(0, 104)
    assert i.low == 0.0
    assert i.high == pytest.approx(0.0356, abs=0.0005)


def test_all_of_n_has_a_lower_bound_below_one():
    i = wilson(13, 13)
    assert i.high == 1.0
    assert i.low == pytest.approx(0.772, abs=0.001)


def test_half_is_centred():
    i = wilson(5, 10)
    assert i.rate == 0.5
    assert i.low == pytest.approx(0.2366, abs=0.0005)
    assert i.high == pytest.approx(0.7634, abs=0.0005)


def test_no_total_is_the_whole_range():
    i = wilson(0, 0)
    assert (i.rate, i.low, i.high, i.text()) == (None, 0.0, 1.0, "0/0")


def test_text_gives_count_rate_and_interval():
    assert wilson(1, 4).text() == "1/4 (25.0%, 95% CI 4.6%-69.9%)"


@pytest.mark.parametrize(("hits", "total"), [(-1, 3), (4, 3)])
def test_impossible_counts_are_refused(hits, total):
    with pytest.raises(ValueError):
        wilson(hits, total)
