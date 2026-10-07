"""Typed evaluation design, frozen into ``experiment.evaluation_design`` at preregistration.

Metric names and directions come from ``Game.outcome_metrics``; performance metrics from
``Game.performance_guardrails``. The experiment adds primary metrics and agent guardrails.
The candidate is not part of the design: it is bound once after upload through
``experiment.candidate_binding`` and passed to ``pair()`` / ``evaluate()``.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import jsonschema

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "evaluation_design.json"


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


# Opponent entries that the platform samples per request instead of a fixed policy ref.
OPPONENT_SELECTORS = ("random",)


class DesignError(ValueError):
    """A stored or constructed evaluation design is malformed or inconsistent."""


@dataclass(frozen=True)
class MetricSpec:
    name: str
    direction: Literal["up", "down"]
    role: Literal["primary", "agent_guardrail", "performance"]
    min_effect: float | None
    tolerance: float | None


@dataclass(frozen=True)
class EvaluationDesign:
    game_version: str
    baseline_version: str
    seats: tuple[int, ...]
    opponents: tuple[str, ...]
    seeds: tuple[int, ...]
    metrics: tuple[MetricSpec, ...]
    k: float
    delta: dict[str, float]
    n_min: int
    noise_rev: str

    @property
    def pairing(self) -> Literal["seed_and_opponents", "seed_only"]:
        """What a candidate/baseline pair shares on the platform (A12). Each pair plays one
        seed (``game_config_overrides.seed``); opponents are identical across the pair only
        when every entry of ``opponents`` is a fixed policy ref. A selector such as
        ``"random"`` is sampled per request, so pairing is then seed-only."""
        if any(entry in OPPONENT_SELECTORS for entry in self.opponents):
            return "seed_only"
        return "seed_and_opponents"

    def validate(self) -> None:
        """Raise DesignError unless there is at least one primary metric, metric names are
        unique, every delta key names a performance metric, and baseline_version is non-empty."""
        problems = []
        if not any(metric.role == "primary" for metric in self.metrics):
            problems.append("no primary metric")
        names = [metric.name for metric in self.metrics]
        for name in sorted({n for n in names if names.count(n) > 1}):
            problems.append(f"duplicate metric name {name!r}")
        performance = {m.name for m in self.metrics if m.role == "performance"}
        for key in sorted(set(self.delta) - performance):
            problems.append(f"delta key {key!r} is not a performance metric")
        if not self.baseline_version.strip():
            problems.append("baseline_version is empty")
        if problems:
            raise DesignError("; ".join(problems))

    def to_dict(self) -> dict:
        """JSON-ready dict (tuples become lists); matches schemas/evaluation_design.json."""
        self.validate()
        data = asdict(self)
        for key in ("seats", "opponents", "seeds", "metrics"):
            data[key] = list(data[key])
        data["delta"] = dict(self.delta)
        try:
            jsonschema.validate(data, _schema())
        except jsonschema.ValidationError as error:
            raise DesignError(error.message) from error
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationDesign":
        try:
            jsonschema.validate(data, _schema())
        except jsonschema.ValidationError as error:
            raise DesignError(error.message) from error
        design = cls(
            game_version=data["game_version"],
            baseline_version=data["baseline_version"],
            seats=tuple(data["seats"]),
            opponents=tuple(data["opponents"]),
            seeds=tuple(data["seeds"]),
            metrics=tuple(MetricSpec(**metric) for metric in data["metrics"]),
            k=data["k"],
            delta=dict(data["delta"]),
            n_min=data["n_min"],
            noise_rev=data["noise_rev"],
        )
        design.validate()
        return design


def signed(value: float, spec: MetricSpec) -> float:
    """Direction-normalise: "down" metrics are negated so larger is always better."""
    return -value if spec.direction == "down" else value
