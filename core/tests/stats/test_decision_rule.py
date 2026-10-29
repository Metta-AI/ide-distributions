"""Decision rule on fixture rows shaped like the GotA contract (glory and win_rate performance).

Metric names here are fixture names only; the rule takes every name from the design.
"""

import subprocess
import sys

import pytest

from research_core.stats.decision_rule import evaluate, verdict_for
from research_core.stats.design import EvaluationDesign, MetricSpec
from research_core.stats.ingest import EpisodeRow

BASE, CAND = "pv-base", "pv-cand"
BASE_VALUES = {
    "xp_per_1k_ticks": 20.0,
    "shots_fired": 30.0,
    "glory": 10.0,
    "win_rate": 0.5,
    "deaths_per_1k_ticks": 4.0,
}


def design(
    primary=("xp_per_1k_ticks", "up", 1.0),
    n_min=10,
    guardrail=("shots_fired", "up", 0.5),
    delta=None,
):
    name, direction, min_effect = primary
    guard_name, guard_direction, tolerance = guardrail
    metrics = (
        MetricSpec(name, direction, "primary", min_effect, None),
        MetricSpec(guard_name, guard_direction, "guardrail", None, tolerance),
        MetricSpec("glory", "up", "performance", None, None),
        MetricSpec("win_rate", "up", "performance", None, None),
    )
    return EvaluationDesign(
        game_version="65",
        baseline_version=BASE,
        candidate_version=CAND,
        seats=(0, 1),
        opponents=("pv-opp",),
        seeds=tuple(range(100)),
        metrics=metrics,
        k=0.5,
        delta={"glory": 0.5, "win_rate": 0.05} if delta is None else delta,
        n_min=n_min,
    )


def pairs(n=20, jitter=0.01, **diffs):
    """n pairs; candidate = baseline + diff, with alternating +/- jitter on each metric."""
    result = []
    for seed in range(n):
        sign = 1 if seed % 2 else -1
        base = dict(BASE_VALUES)
        cand = {k: v + diffs.get(k, 0.0) + sign * jitter for k, v in base.items()}
        result.append(
            (
                EpisodeRow(f"b{seed}", "65", BASE, seed, (0, 1), ("pv-opp",), base, "completed"),
                EpisodeRow(f"c{seed}", "65", CAND, seed, (0, 1), ("pv-opp",), cand, "completed"),
            )
        )
    return result


def test_all_good_is_confirmed():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, glory=0.3))
    assert outcome.verdict == "confirmed" and outcome.reason_codes == []
    primary = outcome.per_metric["xp_per_1k_ticks"]["primary"]
    assert primary["passed"]
    assert primary["ci"][0] > 0 and abs(primary["mean_signed_diff"] - 2.0) < 1e-9
    assert outcome.per_metric["glory"]["performance"]["ci"] is None


def test_glory_up_but_win_rate_down_beyond_delta_is_refuted():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, glory=1.0, win_rate=-0.2))
    assert outcome.verdict == "refuted"
    assert outcome.reason_codes == ["performance_slip:win_rate"]
    assert outcome.per_metric["glory"]["performance"]["passed"]
    assert outcome.per_metric["win_rate"]["performance"]["threshold"] == -0.05


def test_guardrail_breach_is_refuted():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, shots_fired=-3.0))
    assert outcome.verdict == "refuted"
    assert outcome.reason_codes == ["guardrail_breach:shots_fired"]


def test_guardrail_drop_within_tolerance_passes():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, shots_fired=-0.3))
    assert outcome.verdict == "confirmed"


def test_slip_within_delta_with_significant_primary_is_confirmed():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, win_rate=-0.02))
    assert outcome.verdict == "confirmed", outcome.reason_codes
    assert outcome.per_metric["win_rate"]["performance"]["mean_signed_diff"] < 0


def test_slip_within_delta_without_significant_primary_is_refuted():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=0.0, win_rate=-0.02))
    assert outcome.verdict == "refuted"
    assert "primary_not_significant:xp_per_1k_ticks" in outcome.reason_codes
    assert "performance_slip_without_significance:win_rate" in outcome.reason_codes
    assert outcome.per_metric["win_rate"]["performance"]["passed"] is False


def test_mean_above_min_effect_but_ci_spanning_zero_is_not_significant():
    noisy = pairs(n=20, xp_per_1k_ticks=1.5)
    for index, (_, cand) in enumerate(noisy):
        cand.metrics["xp_per_1k_ticks"] += 30.0 if index % 2 else -30.0
    outcome = evaluate(design(), noisy)
    assert outcome.reason_codes == ["primary_not_significant:xp_per_1k_ticks"]
    low, high = outcome.per_metric["xp_per_1k_ticks"]["primary"]["ci"]
    assert low < 0 < high


def test_lower_is_better_primary():
    lower = design(primary=("deaths_per_1k_ticks", "down", 1.0))
    better = evaluate(lower, pairs(deaths_per_1k_ticks=-2.0))
    assert better.verdict == "confirmed", better.reason_codes
    assert better.per_metric["deaths_per_1k_ticks"]["primary"]["mean_signed_diff"] > 0
    worse = evaluate(lower, pairs(deaths_per_1k_ticks=2.0))
    assert worse.reason_codes == ["primary_not_significant:deaths_per_1k_ticks"]


def test_n_short_is_inconclusive():
    outcome = evaluate(design(n_min=40), pairs(n=12, xp_per_1k_ticks=2.0))
    assert outcome.verdict == "inconclusive"
    assert outcome.reason_codes == ["n_short:12<40"]


def test_n_short_is_inconclusive_even_with_a_breach():
    """Too few pairs decide nothing: the breach is reported but does not refute."""
    outcome = evaluate(design(n_min=40), pairs(n=12, xp_per_1k_ticks=2.0, shots_fired=-3.0))
    assert outcome.verdict == "inconclusive"
    assert outcome.reason_codes == ["n_short:12<40", "guardrail_breach:shots_fired"]


def test_no_pairs_is_inconclusive_without_crashing():
    outcome = evaluate(design(), [])
    assert outcome.verdict == "inconclusive"
    assert outcome.reason_codes == ["n_short:0<10"]
    assert outcome.per_metric["glory"]["performance"]["passed"] is False
    assert outcome.per_metric["xp_per_1k_ticks"]["primary"]["ci"] is None


def test_pairs_of_other_policies_are_a_caller_error():
    stray = pairs(xp_per_1k_ticks=2.0)
    stray[3][1].policy_version = "pv-other"
    with pytest.raises(ValueError, match="pv-other"):
        evaluate(design(), stray)
    swapped = [(candidate, baseline) for baseline, candidate in pairs()]
    with pytest.raises(ValueError, match="not the design's"):
        evaluate(design(), swapped)


def test_outcome_is_deterministic_and_serialisable():
    data = pairs(xp_per_1k_ticks=2.0)
    first = evaluate(design(), data).to_dict()
    assert first == evaluate(design(), data).to_dict()
    assert set(first) == {"verdict", "reason_codes", "per_metric"}


def test_no_primary_metric_is_inconclusive():
    without_primary = design()
    object.__setattr__(
        without_primary,
        "metrics",
        tuple(m for m in without_primary.metrics if m.role != "primary"),
    )
    outcome = evaluate(without_primary, pairs())
    assert outcome.verdict == "inconclusive"
    assert outcome.reason_codes == ["no_primary_metric"]


def test_down_direction_guardrail():
    lower = design(guardrail=("deaths_per_1k_ticks", "down", 0.5))
    breach = evaluate(lower, pairs(xp_per_1k_ticks=2.0, deaths_per_1k_ticks=2.0))
    assert breach.reason_codes == ["guardrail_breach:deaths_per_1k_ticks"]
    assert breach.per_metric["deaths_per_1k_ticks"]["guardrail"]["mean_signed_diff"] < 0
    fewer = evaluate(lower, pairs(xp_per_1k_ticks=2.0, deaths_per_1k_ticks=-1.0))
    assert fewer.verdict == "confirmed", fewer.reason_codes


def test_performance_metric_without_delta_allows_no_slip():
    no_delta = design(delta={"glory": 0.5})
    outcome = evaluate(no_delta, pairs(xp_per_1k_ticks=2.0, win_rate=-0.02))
    assert outcome.reason_codes == ["performance_slip:win_rate"]
    assert outcome.per_metric["win_rate"]["performance"]["threshold"] == 0.0
    steady = evaluate(no_delta, pairs(xp_per_1k_ticks=2.0))
    assert steady.verdict == "confirmed", steady.reason_codes


def test_diffs_equal_to_thresholds_pass():
    # exactly representable values, no jitter: primary == min_effect, guardrail == -tolerance,
    # performance == -delta
    exact = pairs(jitter=0.0, xp_per_1k_ticks=1.0, shots_fired=-0.5, glory=-0.5)
    outcome = evaluate(design(), exact)
    assert outcome.verdict == "confirmed", outcome.reason_codes
    assert outcome.per_metric["xp_per_1k_ticks"]["primary"]["mean_signed_diff"] == 1.0
    assert outcome.per_metric["shots_fired"]["guardrail"]["mean_signed_diff"] == -0.5
    assert outcome.per_metric["glory"]["performance"]["mean_signed_diff"] == -0.5


def test_overlapping_metric_roles_keep_every_check():
    shared = design(primary=("glory", "up", 1.0), guardrail=("win_rate", "up", 0.01))
    shared.to_dict()
    outcome = evaluate(shared, pairs(glory=2.0, win_rate=-0.02))
    assert outcome.reason_codes == ["guardrail_breach:win_rate"]
    assert outcome.per_metric["glory"]["primary"]["passed"]
    assert outcome.per_metric["glory"]["performance"]["passed"]
    assert not outcome.per_metric["win_rate"]["guardrail"]["passed"]
    assert outcome.per_metric["win_rate"]["performance"]["passed"]


def test_overlapping_primary_is_not_overwritten_by_performance():
    shared = design(primary=("glory", "up", 1.0))
    outcome = evaluate(shared, pairs(glory=0.2, win_rate=-0.02))
    assert "primary_not_significant:glory" in outcome.reason_codes
    assert "performance_slip_without_significance:win_rate" in outcome.reason_codes


# --- the verdict mapping over reason codes ---


@pytest.mark.parametrize(
    "codes, expected",
    [
        ([], "confirmed"),
        (["n_short:3<10"], "inconclusive"),
        (["n_short:3<10", "primary_not_significant:xp"], "inconclusive"),
        (["n_short:3<10", "guardrail_breach:shots"], "inconclusive"),
        (["no_primary_metric", "n_short:0<10"], "inconclusive"),
        (["no_primary_metric"], "inconclusive"),
        (["primary_not_significant:xp"], "refuted"),
        (["guardrail_breach:shots"], "refuted"),
        (["performance_slip:win_rate"], "refuted"),
        (["performance_slip_without_significance:win_rate"], "refuted"),
        (["primary_not_significant:xp", "performance_slip_without_significance:w"], "refuted"),
        (["guardrail_breach:shots", "performance_slip:glory"], "refuted"),
    ],
)
def test_verdict_for_maps_reason_codes(codes, expected):
    assert verdict_for(codes) == expected


def test_importing_the_rule_does_not_load_numpy():
    """numpy is imported inside the bootstrap, so the design and rule modules stay light."""
    code = (
        "import sys, research_core.stats.decision_rule, research_core.stats.design;"
        " assert 'numpy' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
