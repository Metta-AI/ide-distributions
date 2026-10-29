"""The pre-registered decision rule: does the paired evidence confirm or refute the candidate?

All metric names, directions, effects, tolerances and deltas come from the design. Differences
are candidate minus baseline after ``signed()``, so larger is always better.

Reason codes, in the order they are checked:

- ``no_primary_metric`` - the design has no primary metric, so nothing can be confirmed
  (``EvaluationDesign.validate`` refuses such a design; only a hand-built one gets here).
- ``n_short:<n><<n_min>`` - fewer pairs than ``design.n_min``.
- ``primary_not_significant:<name>`` - a primary metric's mean signed diff is below its
  ``min_effect``, or its paired bootstrap CI does not lie entirely above 0.
- ``guardrail_breach:<name>`` - a guardrail's mean signed diff is below ``-tolerance``.
- ``performance_slip:<name>`` - a performance metric's mean signed diff is below
  ``-delta[name]`` (below 0 when the design gives it no delta).
- ``performance_slip_without_significance:<name>`` - a performance metric slipped (within
  delta) but no primary metric is significant, so no slip is allowed.

With no pairs at all only ``n_short`` (and possibly ``no_primary_metric``) is reported.

The verdict follows from the codes, first match wins: no codes -> ``confirmed``; an ``n_short``
code -> ``inconclusive`` (too few pairs decide nothing, whatever the means show); any
``primary_not_significant``, ``guardrail_breach`` or ``performance_slip*`` code -> ``refuted``;
anything else (only ``no_primary_metric``) -> ``inconclusive``.
"""

import statistics
from dataclasses import asdict, dataclass, field
from typing import Literal

from research_core.stats.bootstrap import paired_bootstrap_ci
from research_core.stats.design import EvaluationDesign, MetricSpec, signed
from research_core.stats.ingest import EpisodeRow

BOOTSTRAP_SEED = 0

VerdictName = Literal["confirmed", "refuted", "inconclusive"]
VERDICTS: tuple[VerdictName, ...] = ("confirmed", "refuted", "inconclusive")
REFUTING_CODES = frozenset(
    {
        "primary_not_significant",
        "guardrail_breach",
        "performance_slip",
        "performance_slip_without_significance",
    }
)


@dataclass
class Verdict:
    verdict: VerdictName
    reason_codes: list[str] = field(default_factory=list)
    # per_metric[name][role] = {mean_signed_diff, ci, threshold, passed}
    per_metric: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def verdict_for(reason_codes: list[str]) -> VerdictName:
    """The verdict the reason codes imply; see the module docstring for the precedence."""
    kinds = [code.split(":", 1)[0] for code in reason_codes]
    if not kinds:
        return "confirmed"
    if "n_short" in kinds:
        return "inconclusive"
    if any(kind in REFUTING_CODES for kind in kinds):
        return "refuted"
    return "inconclusive"


def _signed_diffs(pairs: list[tuple[EpisodeRow, EpisodeRow]], spec: MetricSpec) -> list[float]:
    return [
        signed(candidate.metrics[spec.name], spec) - signed(baseline.metrics[spec.name], spec)
        for baseline, candidate in pairs
    ]


def _threshold(spec: MetricSpec, design: EvaluationDesign) -> float:
    if spec.role == "primary":
        return spec.min_effect or 0.0
    if spec.role == "guardrail":
        return -spec.tolerance if spec.tolerance else 0.0
    # A performance metric without a delta allows no slip at all.
    return -design.delta[spec.name] if design.delta.get(spec.name) else 0.0


def evaluate(design: EvaluationDesign, pairs: list[tuple[EpisodeRow, EpisodeRow]]) -> Verdict:
    """Apply the decision rule to ``pairs`` (from ``ingest.pair``); see the module docstring.

    Every pair must be ``(baseline_row, candidate_row)`` for the design's two versions; a row
    of another policy on either side is a caller error (``ValueError``).
    """
    for baseline, candidate in pairs:
        if (baseline.policy_version, candidate.policy_version) != (
            design.baseline_version,
            design.candidate_version,
        ):
            raise ValueError(
                f"pair ({baseline.episode_id}, {candidate.episode_id}) plays "
                f"({baseline.policy_version!r}, {candidate.policy_version!r}), not the design's "
                f"({design.baseline_version!r}, {design.candidate_version!r})"
            )
    reason_codes = []
    if not any(spec.role == "primary" for spec in design.metrics):
        reason_codes.append("no_primary_metric")
    if len(pairs) < design.n_min:
        reason_codes.append(f"n_short:{len(pairs)}<{design.n_min}")

    per_metric: dict[str, dict] = {}
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
        per_metric.setdefault(spec.name, {})[spec.role] = {
            "mean_signed_diff": mean,
            "ci": ci,
            "threshold": threshold,
            "passed": passed,
        }

    any_primary_significant = any(
        per_metric[spec.name][spec.role]["passed"]
        for spec in design.metrics
        if spec.role == "primary"
    )
    for spec in design.metrics:
        name, role = spec.name, spec.role
        entry = per_metric[name][role]
        if entry["mean_signed_diff"] is None:
            continue  # no pairs at all: n_short already blocks
        if role == "primary" and not entry["passed"]:
            reason_codes.append(f"primary_not_significant:{name}")
        elif role == "guardrail" and not entry["passed"]:
            reason_codes.append(f"guardrail_breach:{name}")
        elif role == "performance":
            if not entry["passed"]:
                reason_codes.append(f"performance_slip:{name}")
            elif entry["mean_signed_diff"] < 0 and not any_primary_significant:
                entry["passed"] = False
                reason_codes.append(f"performance_slip_without_significance:{name}")

    return Verdict(
        verdict=verdict_for(reason_codes), reason_codes=reason_codes, per_metric=per_metric
    )
