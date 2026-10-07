import math

import pytest
import yaml

from autoresearch.games.loader import Game, GameMetric
from autoresearch.io.gitops import git
from autoresearch.paths import Layout
from autoresearch.stats.bootstrap import paired_bootstrap_ci
from autoresearch.stats.freeze import freeze_design
from autoresearch.stats.ingest import EpisodeRow
from autoresearch.stats.noise import NoiseError, compute_noise, load_noise, sd_for, write_noise
from autoresearch.stats.power import delta, min_episodes


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


def test_delta_and_cap():
    assert delta(10, 100, k=0.5) == pytest.approx(0.5)
    assert delta(10, 100, k=3.0) == pytest.approx(1.0)  # capped at k_cap=1.0


def test_min_episodes_and_floor():
    assert min_episodes(10, 2) == 197
    assert min_episodes(1, 2) == 40
    assert min_episodes(1, 2, floor=5) == 5


def test_compute_noise_marginal_and_paired():
    record = compute_noise(paired_rows(20), ["score"], "65", ["EV-1"])
    entry = record["metrics"]["score"]
    assert record["game_version"] == "65" and record["evidence"] == ["EV-1"]
    assert entry["n"] == 40 and entry["n_pairs"] == 20
    assert entry["marginal_sd"] > 0
    # diffs alternate 2 and 3 -> sample sd of that sequence
    expected = math.sqrt(20 * 0.25 / 19)
    assert entry["paired_diff_sd"] == pytest.approx(expected)


def test_compute_noise_without_pairs():
    rows = [row(f"e{i}", "pv-a", i, float(i)) for i in range(5)]
    entry = compute_noise(rows, ["score"], "65", [])["metrics"]["score"]
    assert entry["paired_diff_sd"] is None and entry["n_pairs"] == 0


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


@pytest.fixture
def layout(tmp_path):
    git(tmp_path, "init", "-q")
    return Layout(tmp_path)


def test_write_load_and_history(layout):
    first = compute_noise(paired_rows(20), ["score"], "65", [])
    rev1 = write_noise(layout, first)
    path = layout.stats / "metric-noise.yaml"
    assert rev1 == git(layout.root, "hash-object", str(path))
    assert load_noise(layout, "65")["metrics"] == first["metrics"]
    assert not (layout.stats / "history").exists()

    second = compute_noise(paired_rows(30), ["score"], "65", [])
    rev2 = write_noise(layout, second)
    assert rev2 != rev1
    saved = list((layout.stats / "history").glob("*.yaml"))
    assert len(saved) == 1
    assert yaml.safe_load(saved[0].read_text())["metrics"] == first["metrics"]


def test_load_noise_errors(layout):
    with pytest.raises(NoiseError, match="no noise record"):
        load_noise(layout, "65")
    write_noise(layout, compute_noise(paired_rows(20), ["score"], "65", []))
    with pytest.raises(NoiseError, match="66"):
        load_noise(layout, "66")


def test_bootstrap_reproducible_and_sensible():
    diffs = [1.0, 2.0, 3.0, 2.5, 1.5, 2.2, 1.8, 2.9]
    assert paired_bootstrap_ci(diffs, seed=3) == paired_bootstrap_ci(diffs, seed=3)
    low, high = paired_bootstrap_ci(diffs, iters=2000)
    assert low < sum(diffs) / len(diffs) < high
    with pytest.raises(ValueError):
        paired_bootstrap_ci([])


def make_game():
    game = Game.__new__(Game)
    object.__setattr__(
        game, "outcome_metrics", [GameMetric("glory", "up"), GameMetric("deaths", "down")]
    )
    object.__setattr__(game, "performance_guardrails", ["glory"])
    object.__setattr__(game, "min_episodes_floor", 40)
    return game


def noise_record():
    entry = lambda sd: {
        "mean": 0.0,
        "marginal_sd": sd,
        "paired_diff_sd": None,
        "n": 500,
        "n_pairs": 0,
    }
    return {
        "game_version": "65",
        "computed_at": "t",
        "evidence": [],
        "metrics": {"glory": entry(10 / math.sqrt(2)), "hits": entry(10 / math.sqrt(2))},
    }


META = {
    "evaluation_design_request": {
        "baseline_version": "pv-base",
        "seats": [0, 1],
        "opponents": ["pv-opp"],
        "seeds": [1, 2, 3],
        "primary_metrics": [{"name": "hits", "direction": "up", "min_effect": 2.0}],
        "guardrails": [{"name": "deaths", "direction": "down", "tolerance": 0.05}],
    }
}


def test_freeze_design_values(layout):
    design = freeze_design(META, make_game(), noise_record(), 100, noise_rev="rev1")
    assert design.delta == {"glory": pytest.approx(0.5)}
    assert design.n_min == 197 and design.noise_rev == "rev1" and design.k == 0.5
    roles = {m.name: m.role for m in design.metrics}
    assert roles == {"hits": "primary", "deaths": "agent_guardrail", "glory": "performance"}
    assert design.game_version == "65"


def test_freeze_design_noise_rev_from_record():
    record = {**noise_record(), "_rev": "from-record"}
    assert freeze_design(META, make_game(), record, 100).noise_rev == "from-record"
    with pytest.raises(NoiseError):
        freeze_design(META, make_game(), noise_record(), 100)


def test_frozen_design_unchanged_after_later_write_noise(layout):
    write_noise(layout, noise_record())
    rev = write_noise(layout, noise_record())
    frozen = freeze_design(
        META, make_game(), load_noise(layout, "65"), 100, noise_rev=rev
    ).to_dict()
    snapshot = repr(frozen)
    louder = noise_record()
    louder["metrics"]["glory"]["marginal_sd"] *= 5
    write_noise(layout, louder)
    assert repr(frozen) == snapshot
    assert frozen["delta"]["glory"] == pytest.approx(0.5)
