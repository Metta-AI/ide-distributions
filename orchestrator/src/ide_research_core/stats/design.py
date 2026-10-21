"""Typed evaluation design, frozen into ``experiment.evaluation_design`` at preregistration.

Metric names and directions come from ``Game.outcome_metrics``; performance metrics from
``Game.performance_guardrails``. The experiment adds primary metrics and agent guardrails.
The candidate is not part of the design: it is bound once after upload through
``experiment.candidate_binding`` and passed to ``pair()`` / ``evaluate()``.

Opponents (A12). Normally ``lineups`` holds one opponent line-up per seed, sampled from the
league at design time (``stats.opponents``), with the ``league_snapshot`` it came from; both
arms of a seed's pair face that line-up, and ``opponents`` is empty. A narrow fixed set in
``opponents`` (every seed faces it) is allowed only with an ``opponent_rationale``, checked
when the design is frozen (``stats.freeze``); designs frozen before A12's revision have a fixed
``opponents`` and no rationale, and still load. ``lineups``, ``league_snapshot`` and
``opponent_rationale`` are left out of ``to_dict`` when unset, so older designs keep their
hash (``design_sha256``).
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import jsonschema

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "evaluation_design.json"


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
    lineups: dict[str, tuple[str, ...]] | None = None  # seed (as a string) -> opponents
    league_snapshot: dict | None = None  # what the line-ups were sampled from (A12)
    opponent_rationale: str | None = None  # why a fixed ``opponents`` set (A12)

    def opponents_for(self, seed: int) -> tuple[str, ...]:
        """The opponents both arms of ``seed``'s pair face: its line-up, else ``opponents``.
        A seed outside a sampled design has none (``()``); ingest drops its rows as
        ``wrong_seed``."""
        if self.lineups is not None:
            return self.lineups.get(str(seed), ())
        return self.opponents

    @property
    def pairing(self) -> Literal["seed_and_opponents", "seed_only"]:
        """What a candidate/baseline pair shares on the platform (A12). Each pair plays one
        seed (``game_config_overrides.seed``); opponents are identical across the pair only
        when every opponent entry is a fixed policy ref. A selector such as ``"random"`` is
        sampled per request, so pairing is then seed-only."""
        sampled = [entry for lineup in (self.lineups or {}).values() for entry in lineup]
        entries = [*self.opponents, *sampled]
        if any(entry in OPPONENT_SELECTORS for entry in entries):
            return "seed_only"
        return "seed_and_opponents"

    def validate(self) -> None:
        """Raise DesignError unless there is at least one primary metric, metric name/role pairs are
        unique, every delta key names a performance metric, baseline_version is non-empty, seeds
        are unique, and there are at least ``n_min`` seeds (each seed is one candidate/baseline
        pair of one-episode XP requests, A12, so fewer seeds can never reach N)."""
        problems = []
        if not any(metric.role == "primary" for metric in self.metrics):
            problems.append("no primary metric")
        keys = [(metric.name, metric.role) for metric in self.metrics]
        for name, role in sorted({key for key in keys if keys.count(key) > 1}):
            problems.append(f"duplicate metric role {name!r}/{role}")
        for name in sorted({metric.name for metric in self.metrics}):
            directions = {metric.direction for metric in self.metrics if metric.name == name}
            if len(directions) != 1:
                problems.append(f"conflicting directions for metric {name!r}")
        performance = {m.name for m in self.metrics if m.role == "performance"}
        for key in sorted(set(self.delta) - performance):
            problems.append(f"delta key {key!r} is not a performance metric")
        if not self.baseline_version.strip():
            problems.append("baseline_version is empty")
        for seed in sorted({s for s in self.seeds if self.seeds.count(s) > 1}):
            problems.append(f"duplicate seed {seed}")
        if len(set(self.seeds)) < self.n_min:
            problems.append(
                f"{len(set(self.seeds))} unique seeds < n_min {self.n_min}: each seed is one "
                f"pair, so add seeds"
            )
        if self.lineups is not None:
            if self.opponents:
                problems.append("lineups and a fixed opponents set are exclusive")
            if set(self.lineups) != {str(seed) for seed in self.seeds}:
                problems.append("lineups must have exactly one line-up per seed")
            if len({len(lineup) for lineup in self.lineups.values()}) > 1:
                problems.append("every line-up must have the same number of opponents")
            if self.league_snapshot is None:
                problems.append("sampled lineups need the league_snapshot they came from")
        if problems:
            raise DesignError("; ".join(problems))

    def to_dict(self) -> dict:
        """JSON-ready dict (tuples become lists); matches schemas/evaluation_design.json."""
        self.validate()
        data = asdict(self)
        for key in ("seats", "opponents", "seeds", "metrics"):
            data[key] = list(data[key])
        data["delta"] = dict(self.delta)
        for key in ("lineups", "league_snapshot", "opponent_rationale"):
            if data[key] is None:
                del data[key]
        if self.lineups is not None:
            data["lineups"] = {seed: list(lineup) for seed, lineup in self.lineups.items()}
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
            lineups=(
                {seed: tuple(lineup) for seed, lineup in data["lineups"].items()}
                if "lineups" in data
                else None
            ),
            league_snapshot=data.get("league_snapshot"),
            opponent_rationale=data.get("opponent_rationale"),
        )
        design.validate()
        return design


def signed(value: float, spec: MetricSpec) -> float:
    """Direction-normalise: "down" metrics are negated so larger is always better."""
    return -value if spec.direction == "down" else value


def design_sha256(design: EvaluationDesign) -> str:
    """The hash ``stats evaluate`` stores with an outcome, so a promotion can tell that the
    outcome was computed for the frozen design it is about to submit."""
    canonical = json.dumps(design.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
