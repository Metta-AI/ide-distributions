"""Typed evaluation design: what an Experiment's methods fix before any episode is played.

An ``EvaluationDesign`` names the two Versions under comparison (any Version may be the
baseline), the game version, the seats we play, the opponents, the seeds (each seed is one
candidate/baseline pair of one-episode XP requests), and the metrics with the thresholds the
decision rule applies: ``min_effect`` for a primary metric, ``tolerance`` for a guardrail,
``delta`` (allowed slip, from ``power.delta``) for a performance metric, and ``n_min`` (pairs
needed, from ``power.min_episodes``).

Opponents. Normally ``lineups`` holds one opponent line-up per seed, sampled from the league at
design time, with the ``league_snapshot`` it came from; both arms of a seed's pair face that
line-up and ``opponents`` is empty. A fixed ``opponents`` set (every seed faces it) is the
alternative, explained by ``opponent_rationale``. ``lineups``, ``league_snapshot`` and
``opponent_rationale`` are left out of ``to_dict`` when unset.

``from_dict`` checks the dict's shape (keys and types) in plain code and ``validate`` checks
the design's consistency; both raise ``DesignError`` naming every problem at once. The metric
role ``agent_guardrail`` is the old name of ``guardrail``; it is accepted on input and written
back as ``guardrail``.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

Direction = Literal["up", "down"]
Role = Literal["primary", "guardrail", "performance"]

DIRECTIONS: tuple[Direction, ...] = ("up", "down")
ROLES: tuple[Role, ...] = ("primary", "guardrail", "performance")
ROLE_ALIASES = {"agent_guardrail": "guardrail"}

# Opponent entries that the platform samples per request instead of a fixed policy ref.
OPPONENT_SELECTORS = ("random",)


class DesignError(ValueError):
    """A stored or constructed evaluation design is malformed or inconsistent."""


@dataclass(frozen=True)
class MetricSpec:
    name: str
    direction: Direction
    role: Role
    min_effect: float | None = None  # primary: the smallest improvement worth confirming
    tolerance: float | None = None  # guardrail: how far the metric may drop


@dataclass(frozen=True)
class EvaluationDesign:
    game_version: str
    baseline_version: str
    candidate_version: str
    seats: tuple[int, ...]
    opponents: tuple[str, ...]
    seeds: tuple[int, ...]
    metrics: tuple[MetricSpec, ...]
    k: float
    delta: dict[str, float]
    n_min: int
    lineups: dict[str, tuple[str, ...]] | None = None  # seed (as a string) -> opponents
    league_snapshot: dict | None = None  # what the line-ups were sampled from
    opponent_rationale: str | None = None  # why a fixed ``opponents`` set

    def opponents_for(self, seed: int) -> tuple[str, ...]:
        """The opponents both arms of ``seed``'s pair face: its line-up, else ``opponents``.
        A seed outside a sampled design has none (``()``); ingest drops its rows as
        ``wrong_seed``."""
        if self.lineups is not None:
            return self.lineups.get(str(seed), ())
        return self.opponents

    @property
    def pairing(self) -> Literal["seed_and_opponents", "seed_only"]:
        """What a candidate/baseline pair shares on the platform. Each pair plays one seed
        (``game_config_overrides.seed``); opponents are identical across the pair only when
        every opponent entry is a fixed policy ref. A selector such as ``"random"`` is sampled
        per request, so pairing is then seed-only."""
        sampled = [entry for lineup in (self.lineups or {}).values() for entry in lineup]
        entries = [*self.opponents, *sampled]
        if any(entry in OPPONENT_SELECTORS for entry in entries):
            return "seed_only"
        return "seed_and_opponents"

    def validate(self) -> None:
        """Raise DesignError unless the design is consistent: named, distinct baseline and
        candidate; at least one primary metric; known roles and directions; unique metric
        name/role pairs that agree on direction; every delta key a performance metric; ``k``
        positive; ``n_min`` at least 1; unique seeds, at least ``n_min`` of them (each seed is
        one pair, so fewer can never reach N); and consistent line-ups."""
        problems: list[str] = []
        for label in ("game_version", "baseline_version", "candidate_version"):
            if not getattr(self, label).strip():
                problems.append(f"{label} is empty")
        if self.candidate_version == self.baseline_version:
            problems.append("candidate_version equals baseline_version")
        if not any(metric.role == "primary" for metric in self.metrics):
            problems.append("no primary metric")
        for metric in self.metrics:
            if not metric.name.strip():
                problems.append("metric with an empty name")
            if metric.direction not in DIRECTIONS:
                problems.append(f"metric {metric.name!r}: unknown direction {metric.direction!r}")
            if metric.role not in ROLES:
                problems.append(f"metric {metric.name!r}: unknown role {metric.role!r}")
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
        if not self.k > 0:
            problems.append(f"k must be positive, got {self.k}")
        if self.n_min < 1:
            problems.append(f"n_min must be at least 1, got {self.n_min}")
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
        if self.opponent_rationale is not None and not self.opponent_rationale.strip():
            problems.append("opponent_rationale is empty")
        if problems:
            raise DesignError("; ".join(problems))

    def to_dict(self) -> dict:
        """JSON-ready dict (tuples become lists); ``from_dict`` reads it back."""
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
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationDesign":
        """Build and validate a design from ``to_dict`` output (or a hand-written dict)."""
        problems = _shape_problems(data)
        if problems:
            raise DesignError("; ".join(problems))
        design = cls(
            game_version=data["game_version"],
            baseline_version=data["baseline_version"],
            candidate_version=data["candidate_version"],
            seats=tuple(data["seats"]),
            opponents=tuple(data["opponents"]),
            seeds=tuple(data["seeds"]),
            metrics=tuple(_metric_from_dict(metric) for metric in data["metrics"]),
            k=data["k"],
            delta=dict(data["delta"]),
            n_min=data["n_min"],
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


# --- shape checks (the old JSON schema, in plain code) ---


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_dict(value: object) -> bool:
    return isinstance(value, dict)


def _list_of(value: object, item_ok: Callable[[object], bool]) -> bool:
    return isinstance(value, list) and all(item_ok(item) for item in value)


def _dict_of(value: object, item_ok: Callable[[object], bool]) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and item_ok(item) for key, item in value.items()
    )


def _number_or_none(value: object) -> bool:
    return value is None or _is_number(value)


_REQUIRED_FIELDS = (
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
)
# key -> (accepts the value, what the key must hold), for required and optional keys alike.
_FIELDS: dict[str, tuple[Callable[[object], bool], str]] = {
    "game_version": (_is_str, "a string"),
    "baseline_version": (_is_str, "a string"),
    "candidate_version": (_is_str, "a string"),
    "seats": (lambda v: _list_of(v, _is_int), "a list of integers"),
    "opponents": (lambda v: _list_of(v, _is_str), "a list of strings"),
    "seeds": (lambda v: _list_of(v, _is_int), "a list of integers"),
    "metrics": (lambda v: _list_of(v, _is_dict), "a list of objects"),
    "k": (_is_number, "a number"),
    "delta": (lambda v: _dict_of(v, _is_number), "an object of numbers"),
    "n_min": (_is_int, "an integer"),
    "lineups": (lambda v: _dict_of(v, lambda x: _list_of(x, _is_str)), "an object of string lists"),
    "league_snapshot": (_is_dict, "an object"),
    "opponent_rationale": (_is_str, "a string"),
}
_METRIC_FIELDS: dict[str, tuple[Callable[[object], bool], str]] = {
    "name": (_is_str, "a string"),
    "direction": (_is_str, "a string"),
    "role": (_is_str, "a string"),
    "min_effect": (_number_or_none, "a number or null"),
    "tolerance": (_number_or_none, "a number or null"),
}
_REQUIRED_METRIC_FIELDS = ("name", "direction", "role")
_REQUIRED_SNAPSHOT_FIELDS = ("league_id", "taken_at", "basis", "entries")


def _key_problems(
    data: dict,
    fields: dict[str, tuple[Callable[[object], bool], str]],
    required: tuple[str, ...],
    where: str,
) -> list[str]:
    problems = [f"{where}missing key {key!r}" for key in required if key not in data]
    for key, value in data.items():
        if key not in fields:
            problems.append(f"{where}unknown key {key!r}")
            continue
        accepts, expected = fields[key]
        if not accepts(value):
            problems.append(f"{where}{key} must be {expected}")
    return problems


def _shape_problems(data: object) -> list[str]:
    """Every way ``data`` fails to be a design dict: keys and types only (``validate`` checks
    the values)."""
    if not isinstance(data, dict):
        return ["design must be an object"]
    problems = _key_problems(data, _FIELDS, _REQUIRED_FIELDS, "")
    for index, metric in enumerate(data.get("metrics") or []):
        if isinstance(metric, dict):
            where = f"metrics[{index}]: "
            problems += _key_problems(metric, _METRIC_FIELDS, _REQUIRED_METRIC_FIELDS, where)
    snapshot = data.get("league_snapshot")
    if isinstance(snapshot, dict):
        for key in _REQUIRED_SNAPSHOT_FIELDS:
            if key not in snapshot:
                problems.append(f"league_snapshot: missing key {key!r}")
    return problems


def _metric_from_dict(metric: dict) -> MetricSpec:
    role = metric["role"]
    return MetricSpec(
        name=metric["name"],
        direction=metric["direction"],
        role=ROLE_ALIASES.get(role, role),
        min_effect=metric.get("min_effect"),
        tolerance=metric.get("tolerance"),
    )
