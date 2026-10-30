"""Allowed slip (delta) and pairs needed (min_episodes) from a paired-difference sd."""

import pytest

from research_core.stats.power import delta, min_episodes


def test_delta_and_cap():
    assert delta(10, 100, k=0.5) == pytest.approx(0.5)
    assert delta(10, 100, k=3.0) == pytest.approx(1.0)  # capped at k_cap=1.0
    assert delta(10, 100, k=3.0, k_cap=2.0) == pytest.approx(2.0)


def test_min_episodes_and_floor():
    assert min_episodes(10, 2) == 197
    assert min_episodes(1, 2) == 40
    assert min_episodes(1, 2, floor=5) == 5
    assert min_episodes(10, 2, power=0.9) > 197


def test_delta_and_min_episodes_reject_bad_inputs():
    for bad in (0, -1, float("nan")):
        with pytest.raises(ValueError, match="sd"):
            delta(bad, 100)
        with pytest.raises(ValueError, match="sd"):
            min_episodes(bad, 2)
        with pytest.raises(ValueError, match="min_effect"):
            min_episodes(10, bad)
    with pytest.raises(ValueError, match="n_planned"):
        delta(10, 0)
