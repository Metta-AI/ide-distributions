"""League-like opponent sampling (amendment A12)."""

from collections import Counter

import pytest

from ide_research_core.stats.design import DesignError
from ide_research_core.stats.opponents import (
    OUT_OF_DIVISION_WEIGHT,
    league_snapshot,
    sample_lineups,
)

US, OUR_POLICY = "ply_us", "us:v3"
AT = "2026-10-09T12:00:00+00:00"
STANDINGS = [
    {"player_id": US, "division_id": "div_b", "rank": 3},
    {"player_id": "ply_1", "division_id": "div_b", "rank": 1},
    {"player_id": "ply_2", "division_id": "div_b", "rank": 2},
    {"player_id": "ply_3", "division_id": "div_b", "rank": 4},
    {"player_id": "ply_4", "division_id": "div_a", "rank": 1},
]


def seat(position, policy, player, filler=False):
    return {
        "position": position,
        "policy_version_id": policy,
        "player_id": player,
        "is_filler": filler,
    }


def episode(*seats):
    return {"seats": list(seats)}


LEAGUE_ONLY = [  # episodes without us
    episode(seat(0, "one:v1", "ply_1"), seat(1, "two:v4", "ply_2")),
    episode(seat(0, "one:v1", "ply_1"), seat(1, "four:v9", "ply_4")),
    episode(seat(0, "three:v2", "ply_3"), seat(1, "one:v1", "ply_1", filler=True)),
]
WITH_US = [
    episode(seat(0, OUR_POLICY, US), seat(1, "two:v4", "ply_2")),
    episode(seat(0, OUR_POLICY, US), seat(1, "two:v4", "ply_2")),
    episode(seat(0, OUR_POLICY, None), seat(1, "three:v2", "ply_3")),  # ours by policy
]


def snapshot(episodes, standings=STANDINGS, policy=OUR_POLICY):
    return league_snapshot(episodes, standings, "league_x", US, policy, AT)


def weights(snap) -> dict:
    return {entry["policy"]: entry["weight"] for entry in snap["entries"]}


def test_episodes_with_us_weigh_by_how_often_we_met_each_policy():
    snap = snapshot(LEAGUE_ONLY + WITH_US)
    assert snap["basis"] == "episodes_with_us" and snap["episodes_read"] == 6
    assert weights(snap) == {"two:v4": 2.0, "three:v2": 1.0}
    assert snap["taken_at"] == AT and snap["our_division"] == "div_b"
    assert {"player_id": "ply_4", "division_id": "div_a", "rank": 1} in snap["standings"]


def test_without_episodes_with_us_other_divisions_weigh_less_and_fillers_do_not_count():
    snap = snapshot(LEAGUE_ONLY)
    assert snap["basis"] == "league_episodes"
    assert weights(snap) == {
        "one:v1": 2.0,
        "two:v4": 1.0,
        "three:v2": 1.0,
        "four:v9": OUT_OF_DIVISION_WEIGHT,
    }


def test_without_a_division_every_appearance_counts_the_same():
    snap = snapshot(LEAGUE_ONLY, standings=STANDINGS[1:], policy=None)
    assert snap["our_division"] is None and weights(snap)["four:v9"] == 1.0


def test_lineups_are_per_seed_deterministic_and_distinct_within_a_lineup():
    snap = snapshot(LEAGUE_ONLY)
    seeds = list(range(40))
    lineups = sample_lineups(snap, seeds, 3, "E-1")
    assert lineups == sample_lineups(snap, seeds, 3, "E-1")  # refreezing gives the same
    assert set(lineups) == {str(seed) for seed in seeds}
    assert all(len(set(lineup)) == 3 for lineup in lineups.values())
    assert len({tuple(lineup) for lineup in lineups.values()}) > 1  # seeds differ
    assert lineups != sample_lineups(snap, seeds, 3, "E-2")


def test_lineups_follow_the_weights():
    counts = Counter(
        policy
        for lineup in sample_lineups(snapshot(LEAGUE_ONLY), range(400), 1, "E-1").values()
        for policy in lineup
    )
    assert counts["four:v9"] < counts["two:v4"] / 3 and counts["one:v1"] > counts["two:v4"]


def test_a_lineup_longer_than_the_pool_uses_every_policy_before_repeating():
    [lineup] = sample_lineups(snapshot(WITH_US), [7], 3, "E-1").values()
    assert set(lineup[:2]) == {"two:v4", "three:v2"} and len(lineup) == 3


def test_refusals():
    with pytest.raises(DesignError, match="no opponent"):
        sample_lineups(snapshot([]), [1], 1, "E-1")
    with pytest.raises(DesignError, match="at least 1"):
        sample_lineups(snapshot(LEAGUE_ONLY), [1], 0, "E-1")
