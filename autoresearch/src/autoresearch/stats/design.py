"""Typed evaluation design, frozen into ``experiment.evaluation_design`` at preregistration.

Metric names and directions come from ``Game.outcome_metrics``; performance metrics from
``Game.performance_guardrails``. The experiment adds primary metrics and agent guardrails.
The candidate is not part of the design: it is bound once after upload through
``experiment.candidate_binding`` and passed to ``pair()`` / ``evaluate()``.
"""

from dataclasses import asdict, dataclass
from typing import Literal


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

    def to_dict(self) -> dict:
        """JSON-ready dict (tuples become lists); matches schemas/evaluation_design.json."""
        data = asdict(self)
        for key in ("seats", "opponents", "seeds", "metrics"):
            data[key] = list(data[key])
        data["delta"] = dict(self.delta)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationDesign":
        return cls(
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


def signed(value: float, spec: MetricSpec) -> float:
    """Direction-normalise: "down" metrics are negated so larger is always better."""
    return -value if spec.direction == "down" else value
