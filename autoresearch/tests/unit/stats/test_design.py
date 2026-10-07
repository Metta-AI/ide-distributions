import pytest
from jsonschema import ValidationError, validate

from autoresearch.queue.requests import load_schema
from autoresearch.stats.design import EvaluationDesign, MetricSpec

DESIGN = EvaluationDesign(
    game_version="65",
    baseline_version="pv-base",
    seats=(0, 1),
    opponents=("pv-opp",),
    seeds=(1, 2),
    metrics=(
        MetricSpec("score", "up", "primary", 0.5, None),
        MetricSpec("errors", "down", "performance", None, None),
    ),
    k=0.5,
    delta={"score": 0.2},
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
