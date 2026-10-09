import dataclasses

import pytest
from jsonschema import ValidationError, validate

from autoresearch.queue.requests import load_schema
from autoresearch.stats.design import DesignError, EvaluationDesign, MetricSpec

DESIGN = EvaluationDesign(
    game_version="65",
    baseline_version="pv-base",
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
    noise_rev="abc",
)


def test_round_trip_and_json_types():
    data = DESIGN.to_dict()
    assert data["seats"] == [0, 1] and isinstance(data["metrics"], list)
    assert EvaluationDesign.from_dict(data) == DESIGN


def test_to_dict_matches_schema():
    validate(DESIGN.to_dict(), load_schema("evaluation_design.json"))


def test_schema_rejects_bad_direction():
    data = DESIGN.to_dict()
    data["metrics"][0]["direction"] = "sideways"
    with pytest.raises(ValidationError):
        validate(data, load_schema("evaluation_design.json"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("n_min"),
        lambda d: d["metrics"][0].update(direction="sideways"),
        lambda d: d["metrics"][0].update(role="boss"),
    ],
)
def test_from_dict_rejects_malformed(mutate):
    data = DESIGN.to_dict()
    mutate(data)
    with pytest.raises(DesignError):
        EvaluationDesign.from_dict(data)


def test_from_dict_rejects_inconsistent():
    data = DESIGN.to_dict()
    data["delta"] = {"score": 0.1}  # score is primary, not performance
    with pytest.raises(DesignError):
        EvaluationDesign.from_dict(data)
    data = DESIGN.to_dict()
    data["metrics"].append(dict(data["metrics"][0]))
    with pytest.raises(DesignError):
        EvaluationDesign.from_dict(data)
    data = DESIGN.to_dict()
    data["baseline_version"] = " "
    with pytest.raises(DesignError):
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
    with pytest.raises(DesignError):
        EvaluationDesign.from_dict(data)
    with pytest.raises(ValidationError):
        validate(data, load_schema("evaluation_design.json"))


def test_duplicate_seeds_are_rejected():
    from dataclasses import replace

    with pytest.raises(DesignError, match="duplicate seed"):
        replace(DESIGN, seeds=(1, 1) + tuple(range(2, 12))).validate()
    data = {**DESIGN.to_dict(), "seeds": [1, 1] + list(range(2, 12))}
    with pytest.raises(DesignError):
        EvaluationDesign.from_dict(data)


def test_fewer_seeds_than_n_min_is_rejected():
    """Each seed is one pair (A12), so a design with fewer seeds than n_min can never reach N."""
    from dataclasses import replace

    with pytest.raises(DesignError, match="seeds"):
        replace(DESIGN, seeds=(1, 2, 3), n_min=4).validate()
    with pytest.raises(DesignError, match="seeds"):
        replace(DESIGN, seeds=(), n_min=1).validate()
    replace(DESIGN, seeds=(1, 2, 3, 4), n_min=4).validate()


# --- A12: league-sampled line-ups ---

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
    assert DESIGN.opponents_for(3) == ("pv-opp",)


def test_older_designs_keep_their_shape():
    """Unset A12 fields stay out of the dict, so a design frozen earlier hashes the same."""
    assert not {"lineups", "league_snapshot", "opponent_rationale"} & set(DESIGN.to_dict())


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
