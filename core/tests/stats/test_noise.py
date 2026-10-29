"""The noise record (compute_noise, sd_for) and the paired bootstrap."""

import math

import pytest

from research_core.stats.bootstrap import paired_bootstrap_ci
from research_core.stats.ingest import EpisodeRow
from research_core.stats.noise import NoiseError, compute_noise, sd_for


def row(episode_id, policy, seed, value, version="65", other=0.0):
    return EpisodeRow(
        episode_id=episode_id,
        game_version=version,
        policy_version=policy,
        seed=seed,
        seats=(0,),
        opponents=("opp",),
        metrics={"score": value, "kills": other},
        status="completed",
    )


def paired_rows(count):
    rows = []
    for seed in range(count):
        rows.append(row(f"a{seed}", "pv-a", seed, float(seed)))
        rows.append(row(f"b{seed}", "pv-b", seed, float(seed) + 2.0 + (seed % 2)))
    return rows


def record_with(n_pairs, paired_sd, marginal_sd=4.0):
    entry = {
        "mean": 1.0,
        "marginal_sd": marginal_sd,
        "paired_diff_sd": paired_sd,
        "n": 100,
        "n_pairs": n_pairs,
    }
    return {"game_version": "65", "computed_at": "t", "evidence": [], "metrics": {"score": entry}}


def test_compute_noise_marginal_and_paired():
    record = compute_noise(paired_rows(20), ["score"], "65", ["EV-1"])
    assert set(record) == {"game_version", "computed_at", "evidence", "metrics"}
    entry = record["metrics"]["score"]
    assert record["game_version"] == "65" and record["evidence"] == ["EV-1"]
    assert entry["n"] == 40 and entry["n_pairs"] == 20
    assert entry["marginal_sd"] > 0
    # diffs alternate 2 and 3 -> sample sd of that sequence
    expected = math.sqrt(20 * 0.25 / 19)
    assert entry["paired_diff_sd"] == pytest.approx(expected)


def test_compute_noise_pairs_only_matching_seats_and_opponents():
    rows = paired_rows(4)
    rows.append(row("c0", "pv-c", 0, 100.0))  # a third policy on seed 0 adds two more diffs
    rows.append(row("d0", "pv-d", 0, 5.0, version="65"))
    rows[-1].opponents = ("someone-else",)  # a different line-up never pairs
    entry = compute_noise(rows, ["score"], "65", [])["metrics"]["score"]
    assert entry["n"] == 10 and entry["n_pairs"] == 4 + 2


def test_compute_noise_without_pairs():
    rows = [row(f"e{i}", "pv-a", i, float(i)) for i in range(5)]
    entry = compute_noise(rows, ["score"], "65", [])["metrics"]["score"]
    assert entry["paired_diff_sd"] is None and entry["n_pairs"] == 0


def test_compute_noise_needs_two_rows():
    with pytest.raises(NoiseError, match="at least 2 rows"):
        compute_noise(paired_rows(1)[:1], ["score"], "65", [])


def test_sd_for_paired_when_enough_pairs_else_marginal():
    assert sd_for(record_with(10, 1.5), "score") == 1.5
    assert sd_for(record_with(9, 1.5), "score") == pytest.approx(4.0 * math.sqrt(2))
    assert sd_for(record_with(0, None), "score") == pytest.approx(4.0 * math.sqrt(2))


def test_sd_for_missing_or_zero_raises():
    with pytest.raises(NoiseError):
        sd_for(record_with(0, None), "nope")
    with pytest.raises(NoiseError):
        sd_for(record_with(0, None, marginal_sd=0.0), "score")
    with pytest.raises(NoiseError):
        sd_for(record_with(20, 0.0), "score")


def test_sd_for_rejects_nan():
    with pytest.raises(NoiseError):
        sd_for(record_with(0, None, marginal_sd=float("nan")), "score")


def test_compute_noise_missing_metric_raises():
    with pytest.raises(NoiseError, match="nope"):
        compute_noise(paired_rows(5), ["nope"], "65", [])


def test_bootstrap_reproducible_and_sensible():
    diffs = [1.0, 2.0, 3.0, 2.5, 1.5, 2.2, 1.8, 2.9]
    assert paired_bootstrap_ci(diffs, seed=3) == paired_bootstrap_ci(diffs, seed=3)
    low, high = paired_bootstrap_ci(diffs, iters=2000)
    assert low < sum(diffs) / len(diffs) < high
    assert isinstance(low, float) and isinstance(high, float)
    with pytest.raises(ValueError):
        paired_bootstrap_ci([])
