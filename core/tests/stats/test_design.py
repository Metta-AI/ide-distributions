"""Evaluation design: round trip, plain-code shape checks, consistency rules and line-ups."""

import dataclasses
import re

import pytest

from research_core.stats.design import DesignError, EvaluationDesign, MetricSpec

DESIGN = EvaluationDesign(
    game_version="65",
    baseline_version="pv-base",
    candidate_version="pv-cand",
    seats=(0, 1),
    opponents=("pv-opp",),
    seeds=tuple(range(1, 11)),
    metrics=(
        MetricSpec("score", "up", "primary", 0.5, None),
        MetricSpec("errors", "down", "performance", None, None),
    ),
    k=0.5,
    delta={"errors": 0.2},
    n_min=10,
)
KEYS = {
    "game_version",
    "baseline_version",
    "candidate_version",
    "seats",
    "opponents",
    "seeds",
    "metrics",
    "k",
    "delta",
    "n_min",
}


def test_round_trip_and_json_types():
    data = DESIGN.to_dict()
    assert set(data) == KEYS
    assert data["seats"] == [0, 1] and isinstance(data["metrics"], list)
    assert data["metrics"][0] == {
        "name": "score",
        "direction": "up",
        "role": "primary",
        "min_effect": 0.5,
        "tolerance": None,
    }
    assert EvaluationDesign.from_dict(data) == DESIGN


@pytest.mark.parametrize(
    "mutate, problem",
    [
        (lambda d: d.pop("n_min"), "missing key 'n_min'"),
        (lambda d: d.update(noise_rev="abc"), "unknown key 'noise_rev'"),
        (lambda d: d.update(game_version=65), "game_version must be a string"),
        (lambda d: d.update(seeds=["1", "2"]), "seeds must be a list of integers"),
        (lambda d: d.update(seeds=[True, *range(2, 11)]), "seeds must be a list of integers"),
        (lambda d: d.update(k="0.5"), "k must be a number"),
        (lambda d: d.update(k=0), "k must be positive"),
        (lambda d: d.update(n_min=2.5), "n_min must be an integer"),
        (lambda d: d.update(n_min=0), "n_min must be at least 1"),
        (lambda d: d.update(delta={"errors": "0.2"}), "delta must be an object of numbers"),
        (lambda d: d["metrics"][0].update(direction="sideways"), "unknown direction 'sideways'"),
        (lambda d: d["metrics"][0].update(role="boss"), "unknown role 'boss'"),
        (lambda d: d["metrics"][0].update(bonus=1), "metrics[0]: unknown key 'bonus'"),
        (lambda d: d["metrics"][0].pop("name"), "metrics[0]: missing key 'name'"),
        (lambda d: d["metrics"][0].update(min_effect="big"), "min_effect must be a number or null"),
        (lambda d: d.update(league_snapshot={"league_id": "l"}), "league_snapshot: missing key"),
        (lambda d: d.update(opponent_rationale=" "), "opponent_rationale is empty"),
    ],
)
def test_from_dict_rejects_malformed(mutate, problem):
    data = DESIGN.to_dict()
    mutate(data)
    with pytest.raises(DesignError, match=re.escape(problem)):
        EvaluationDesign.from_dict(data)


def test_from_dict_requires_an_object_and_names_every_problem():
    with pytest.raises(DesignError, match="must be an object"):
        EvaluationDesign.from_dict(["nope"])
    data = DESIGN.to_dict()
    del data["n_min"]
    data["k"] = "x"
    with pytest.raises(DesignError) as info:
        EvaluationDesign.from_dict(data)
    assert "missing key 'n_min'" in str(info.value) and "k must be a number" in str(info.value)


def test_from_dict_rejects_inconsistent():
    data = DESIGN.to_dict()
    data["delta"] = {"score": 0.1}  # score is primary, not performance
    with pytest.raises(DesignError, match="not a performance metric"):
        EvaluationDesign.from_dict(data)
    data = DESIGN.to_dict()
    data["metrics"].append(dict(data["metrics"][0]))
    with pytest.raises(DesignError, match="duplicate metric role"):
        EvaluationDesign.from_dict(data)
    data = DESIGN.to_dict()
    data["baseline_version"] = " "
    with pytest.raises(DesignError, match="baseline_version is empty"):
        EvaluationDesign.from_dict(data)


def test_to_dict_validates():
    bad = EvaluationDesign.from_dict(DESIGN.to_dict())
    object.__setattr__(bad, "delta", {"nope": 1.0})
    with pytest.raises(DesignError):
        bad.to_dict()


def test_design_without_primary_metric_is_rejected():
    no_primary = EvaluationDesign.from_dict(DESIGN.to_dict())
    object.__setattr__(no_primary, "metrics", DESIGN.metrics[1:])
    with pytest.raises(DesignError, match="primary"):
        no_primary.validate()
    data = DESIGN.to_dict()
    data["metrics"] = data["metrics"][1:]
    with pytest.raises(DesignError, match="primary"):
        EvaluationDesign.from_dict(data)


def test_duplicate_seeds_are_rejected():
    with pytest.raises(DesignError, match="duplicate seed"):
        dataclasses.replace(DESIGN, seeds=(1, 1) + tuple(range(2, 12))).validate()
    data = {**DESIGN.to_dict(), "seeds": [1, 1] + list(range(2, 12))}
    with pytest.raises(DesignError, match="duplicate seed"):
        EvaluationDesign.from_dict(data)


def test_fewer_seeds_than_n_min_is_rejected():
    """Each seed is one pair, so a design with fewer seeds than n_min can never reach N."""
    with pytest.raises(DesignError, match="seeds"):
        dataclasses.replace(DESIGN, seeds=(1, 2, 3), n_min=4).validate()
    with pytest.raises(DesignError, match="seeds"):
        dataclasses.replace(DESIGN, seeds=(), n_min=1).validate()
    dataclasses.replace(DESIGN, seeds=(1, 2, 3, 4), n_min=4).validate()


def test_candidate_must_be_named_and_differ_from_the_baseline():
    with pytest.raises(DesignError, match="candidate_version equals baseline_version"):
        dataclasses.replace(DESIGN, candidate_version="pv-base").validate()
    with pytest.raises(DesignError, match="candidate_version is empty"):
        dataclasses.replace(DESIGN, candidate_version=" ").validate()
    # Any Version may be the baseline: swapping the two is a valid design.
    dataclasses.replace(DESIGN, baseline_version="pv-cand", candidate_version="pv-base").validate()


def test_agent_guardrail_is_read_as_guardrail():
    """The old role name is accepted on input and written back under the new name."""
    data = DESIGN.to_dict()
    data["metrics"].append(
        {"name": "shots", "direction": "up", "role": "agent_guardrail", "tolerance": 0.5}
    )
    design = EvaluationDesign.from_dict(data)
    assert design.metrics[-1] == MetricSpec("shots", "up", "guardrail", None, 0.5)
    assert design.to_dict()["metrics"][-1]["role"] == "guardrail"
    assert EvaluationDesign.from_dict(design.to_dict()) == design


def test_metric_thresholds_default_to_none():
    data = DESIGN.to_dict()
    data["metrics"][1] = {"name": "errors", "direction": "down", "role": "performance"}
    assert EvaluationDesign.from_dict(data) == DESIGN


def test_metric_roles_must_agree_on_direction():
    data = DESIGN.to_dict()
    data["metrics"].append(
        {"name": "score", "direction": "down", "role": "guardrail", "tolerance": 0.1}
    )
    with pytest.raises(DesignError, match="conflicting directions"):
        EvaluationDesign.from_dict(data)


# --- league-sampled line-ups ---

SNAPSHOT = {"league_id": "l", "taken_at": "t", "basis": "league_episodes", "entries": []}


def sampled(**changes) -> EvaluationDesign:
    lineups = {str(seed): ("pv-a", f"pv-{seed}") for seed in DESIGN.seeds}
    fields = {"opponents": (), "lineups": lineups, "league_snapshot": SNAPSHOT, **changes}
    return dataclasses.replace(DESIGN, **fields)


def test_sampled_lineups_round_trip_and_give_each_seed_its_own():
    design = sampled()
    data = design.to_dict()
    assert data["lineups"]["3"] == ["pv-a", "pv-3"] and data["league_snapshot"] == SNAPSHOT
    assert EvaluationDesign.from_dict(data) == design
    assert design.opponents_for(3) == ("pv-a", "pv-3") and design.pairing == "seed_and_opponents"
    assert design.opponents_for(99) == ()
    assert DESIGN.opponents_for(3) == ("pv-opp",)


def test_a_sampled_opponent_selector_makes_pairing_seed_only():
    assert dataclasses.replace(DESIGN, opponents=("random",)).pairing == "seed_only"
    lineups = {str(seed): ("pv-a", "random") for seed in DESIGN.seeds}
    assert sampled(lineups=lineups).pairing == "seed_only"


def test_unset_optional_fields_stay_out_of_the_dict():
    assert not {"lineups", "league_snapshot", "opponent_rationale"} & set(DESIGN.to_dict())
    rationale = dataclasses.replace(DESIGN, opponent_rationale="one fixed opponent")
    assert rationale.to_dict()["opponent_rationale"] == "one fixed opponent"
    assert EvaluationDesign.from_dict(rationale.to_dict()) == rationale


@pytest.mark.parametrize(
    "changes, problem",
    [
        ({"opponents": ("pv-opp",)}, "exclusive"),
        ({"lineups": {"1": ("pv-a",)}}, "one line-up per seed"),
        ({"league_snapshot": None}, "league_snapshot"),
    ],
)
def test_inconsistent_lineups_are_rejected(changes, problem):
    with pytest.raises(DesignError, match=problem):
        sampled(**changes).validate()


def test_unequal_lineups_are_rejected():
    lineups = {str(seed): ("pv-a",) for seed in DESIGN.seeds}
    lineups["1"] = ("pv-a", "pv-b")
    with pytest.raises(DesignError, match="same number"):
        sampled(lineups=lineups).validate()
