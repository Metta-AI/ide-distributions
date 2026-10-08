"""The pre-registered decision rule: is the candidate eligible for promotion?

All metric names, directions, effects, tolerances and deltas come from the frozen design.
Differences are candidate minus baseline after ``signed()``, so larger is always better.

The candidate is eligible only when every check passes:

- ``n_short:<n><<n_min>`` - fewer unique pairs than ``design.n_min``.
- ``primary_not_significant:<name>`` - a primary metric's mean signed diff is below its
  ``min_effect``, or its paired bootstrap CI does not lie entirely above 0.
- ``guardrail_breach:<name>`` - an agent guardrail's mean signed diff is below ``-tolerance``.
- ``performance_slip:<name>`` - a performance metric's mean signed diff is below
  ``-delta[name]``.
- ``performance_slip_without_significance:<name>`` - a performance metric slipped (within
  delta) but no primary metric is significant, so no slip is allowed.
- ``baseline_not_champion`` - ``design.baseline_version`` is not the current champion.
- ``no_primary_metric`` - the design has no primary metric, so nothing can justify promotion.

With no pairs at all only ``n_short`` (and possibly ``baseline_not_champion``) is reported.
"""

import statistics
from dataclasses import asdict, dataclass, field

from autoresearch.stats.bootstrap import paired_bootstrap_ci
from autoresearch.stats.design import EvaluationDesign, MetricSpec, signed
from autoresearch.stats.ingest import EpisodeRow

BOOTSTRAP_SEED = 0


@dataclass
class RuleOutcome:
    promote_eligible: bool
    failures: list[str] = field(default_factory=list)
    per_metric: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _signed_diffs(pairs: list[tuple[EpisodeRow, EpisodeRow]], spec: MetricSpec) -> list[float]:
    return [
        signed(candidate.metrics[spec.name], spec) - signed(baseline.metrics[spec.name], spec)
        for baseline, candidate in pairs
    ]


def _threshold(spec: MetricSpec, design: EvaluationDesign) -> float:
    if spec.role == "primary":
        return spec.min_effect or 0.0
    if spec.role == "agent_guardrail":
        return -spec.tolerance if spec.tolerance else 0.0
    # A performance metric without a delta allows no slip at all.
    return -design.delta[spec.name] if design.delta.get(spec.name) else 0.0


def evaluate(
    design: EvaluationDesign,
    pairs: list[tuple[EpisodeRow, EpisodeRow]],
    candidate_platform_id: str,
    current_champion: str,
) -> RuleOutcome:
    """Apply the decision rule to ``pairs`` (from ``pair()``); see the module docstring.

    ``candidate_platform_id`` is the bound candidate; pairs whose candidate row is another
    policy are a caller error.
    """
    if any(candidate.policy_version != candidate_platform_id for _, candidate in pairs):
        raise ValueError(f"pairs contain a candidate other than {candidate_platform_id!r}")
    failures = []
    if not any(spec.role == "primary" for spec in design.metrics):
        failures.append("no_primary_metric")
    if len(pairs) < design.n_min:
        failures.append(f"n_short:{len(pairs)}<{design.n_min}")

    per_metric = {}
    for spec in design.metrics:
        diffs = _signed_diffs(pairs, spec)
        mean = statistics.fmean(diffs) if diffs else None
        ci = None
        if spec.role == "primary" and diffs:
            ci = list(paired_bootstrap_ci(diffs, seed=BOOTSTRAP_SEED))
        threshold = _threshold(spec, design)
        passed = mean is not None and mean >= threshold
        if spec.role == "primary":
            passed = passed and ci[0] > 0
        per_metric[spec.name] = {
            "role": spec.role,
            "mean_signed_diff": mean,
            "ci": ci,
            "threshold": threshold,
            "passed": passed,
        }

    roles = {spec.name: spec.role for spec in design.metrics}
    any_primary_significant = any(
        entry["passed"] for name, entry in per_metric.items() if roles[name] == "primary"
    )
    for name, entry in per_metric.items():
        role = roles[name]
        if entry["mean_signed_diff"] is None:
            continue  # no pairs at all: n_short already blocks
        if role == "primary" and not entry["passed"]:
            failures.append(f"primary_not_significant:{name}")
        elif role == "agent_guardrail" and not entry["passed"]:
            failures.append(f"guardrail_breach:{name}")
        elif role == "performance":
            if not entry["passed"]:
                failures.append(f"performance_slip:{name}")
            elif entry["mean_signed_diff"] < 0 and not any_primary_significant:
                entry["passed"] = False
                failures.append(f"performance_slip_without_significance:{name}")

    if design.baseline_version != current_champion:
        failures.append("baseline_not_champion")
    return RuleOutcome(promote_eligible=not failures, failures=failures, per_metric=per_metric)
