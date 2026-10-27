"""Decision rule on fixture rows shaped like the GotA contract (glory and win_rate performance).

Metric names here are fixture names only; core code takes every name from the design.
"""

from ide_research_core.stats.decision_rule import evaluate
from ide_research_core.stats.design import EvaluationDesign, MetricSpec
from ide_research_core.stats.ingest import EpisodeRow

BASE, CAND, CHAMPION = "pv-base", "pv-cand", "pv-base"
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
        MetricSpec(guard_name, guard_direction, "agent_guardrail", None, tolerance),
        MetricSpec("glory", "up", "performance", None, None),
        MetricSpec("win_rate", "up", "performance", None, None),
    )
    return EvaluationDesign(
        "65",
        BASE,
        (0, 1),
        ("pv-opp",),
        tuple(range(100)),
        metrics,
        0.5,
        {"glory": 0.5, "win_rate": 0.05} if delta is None else delta,
        n_min,
        "rev1",
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


def test_all_good_is_eligible():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, glory=0.3), CAND, CHAMPION)
    assert outcome.submit_eligible and outcome.failures == []
    primary = outcome.per_metric["xp_per_1k_ticks"]["primary"]
    assert primary["passed"]
    assert primary["ci"][0] > 0 and abs(primary["mean_signed_diff"] - 2.0) < 1e-9
    assert outcome.per_metric["glory"]["performance"]["ci"] is None


def test_glory_up_but_win_rate_down_beyond_delta_is_blocked():
    outcome = evaluate(
        design(), pairs(xp_per_1k_ticks=2.0, glory=1.0, win_rate=-0.2), CAND, CHAMPION
    )
    assert not outcome.submit_eligible
    assert outcome.failures == ["performance_slip:win_rate"]
    assert outcome.per_metric["glory"]["performance"]["passed"]
    assert outcome.per_metric["win_rate"]["performance"]["threshold"] == -0.05


def test_guardrail_breach_is_blocked():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, shots_fired=-3.0), CAND, CHAMPION)
    assert not outcome.submit_eligible
    assert outcome.failures == ["guardrail_breach:shots_fired"]


def test_guardrail_drop_within_tolerance_passes():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, shots_fired=-0.3), CAND, CHAMPION)
    assert outcome.submit_eligible


def test_slip_within_delta_with_significant_primary_is_eligible():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0, win_rate=-0.02), CAND, CHAMPION)
    assert outcome.submit_eligible, outcome.failures
    assert outcome.per_metric["win_rate"]["performance"]["mean_signed_diff"] < 0


def test_slip_within_delta_without_significant_primary_is_blocked():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=0.0, win_rate=-0.02), CAND, CHAMPION)
    assert not outcome.submit_eligible
    assert "primary_not_significant:xp_per_1k_ticks" in outcome.failures
    assert "performance_slip_without_significance:win_rate" in outcome.failures


def test_mean_above_min_effect_but_ci_spanning_zero_is_not_significant():
    noisy = pairs(n=20, xp_per_1k_ticks=1.5)
    for index, (_, cand) in enumerate(noisy):
        cand.metrics["xp_per_1k_ticks"] += 30.0 if index % 2 else -30.0
    outcome = evaluate(design(), noisy, CAND, CHAMPION)
    assert outcome.failures == ["primary_not_significant:xp_per_1k_ticks"]
    low, high = outcome.per_metric["xp_per_1k_ticks"]["primary"]["ci"]
    assert low < 0 < high


def test_lower_is_better_primary():
    lower = design(primary=("deaths_per_1k_ticks", "down", 1.0))
    better = evaluate(lower, pairs(deaths_per_1k_ticks=-2.0), CAND, CHAMPION)
    assert better.submit_eligible, better.failures
    assert better.per_metric["deaths_per_1k_ticks"]["primary"]["mean_signed_diff"] > 0
    worse = evaluate(lower, pairs(deaths_per_1k_ticks=2.0), CAND, CHAMPION)
    assert worse.failures == ["primary_not_significant:deaths_per_1k_ticks"]


def test_n_short_is_blocked():
    outcome = evaluate(design(n_min=40), pairs(n=12, xp_per_1k_ticks=2.0), CAND, CHAMPION)
    assert outcome.failures == ["n_short:12<40"]


def test_no_pairs_is_blocked_without_crashing():
    outcome = evaluate(design(), [], CAND, CHAMPION)
    assert not outcome.submit_eligible
    assert "n_short:0<10" in outcome.failures
    assert outcome.per_metric["glory"]["performance"]["passed"] is False


def test_baseline_not_champion_is_blocked():
    outcome = evaluate(design(), pairs(xp_per_1k_ticks=2.0), CAND, "pv-other")
    assert outcome.failures == ["baseline_not_champion"]


def test_outcome_is_deterministic_and_serialisable():
    data = pairs(xp_per_1k_ticks=2.0)
    first = evaluate(design(), data, CAND, CHAMPION).to_dict()
    assert first == evaluate(design(), data, CAND, CHAMPION).to_dict()
    assert set(first) == {"submit_eligible", "failures", "per_metric"}


def test_no_primary_metric_is_blocked():
    without_primary = design()
    object.__setattr__(
        without_primary,
        "metrics",
        tuple(m for m in without_primary.metrics if m.role != "primary"),
    )
    outcome = evaluate(without_primary, pairs(), CAND, CHAMPION)
    assert not outcome.submit_eligible
    assert "no_primary_metric" in outcome.failures


def test_down_direction_guardrail():
    lower = design(guardrail=("deaths_per_1k_ticks", "down", 0.5))
    breach = evaluate(lower, pairs(xp_per_1k_ticks=2.0, deaths_per_1k_ticks=2.0), CAND, CHAMPION)
    assert breach.failures == ["guardrail_breach:deaths_per_1k_ticks"]
    assert breach.per_metric["deaths_per_1k_ticks"]["agent_guardrail"]["mean_signed_diff"] < 0
    fewer = evaluate(lower, pairs(xp_per_1k_ticks=2.0, deaths_per_1k_ticks=-1.0), CAND, CHAMPION)
    assert fewer.submit_eligible, fewer.failures


def test_performance_metric_without_delta_allows_no_slip():
    no_delta = design(delta={"glory": 0.5})
    outcome = evaluate(no_delta, pairs(xp_per_1k_ticks=2.0, win_rate=-0.02), CAND, CHAMPION)
    assert outcome.failures == ["performance_slip:win_rate"]
    assert outcome.per_metric["win_rate"]["performance"]["threshold"] == 0.0
    steady = evaluate(no_delta, pairs(xp_per_1k_ticks=2.0), CAND, CHAMPION)
    assert steady.submit_eligible, steady.failures


def test_diffs_equal_to_thresholds_pass():
    # exactly representable values, no jitter: primary == min_effect, guardrail == -tolerance,
    # performance == -delta
    exact = pairs(jitter=0.0, xp_per_1k_ticks=1.0, shots_fired=-0.5, glory=-0.5)
    outcome = evaluate(design(), exact, CAND, CHAMPION)
    assert outcome.submit_eligible, outcome.failures
    assert outcome.per_metric["xp_per_1k_ticks"]["primary"]["mean_signed_diff"] == 1.0
    assert outcome.per_metric["shots_fired"]["agent_guardrail"]["mean_signed_diff"] == -0.5
    assert outcome.per_metric["glory"]["performance"]["mean_signed_diff"] == -0.5


def test_overlapping_metric_roles_keep_every_check():
    shared = design(primary=("glory", "up", 1.0), guardrail=("win_rate", "up", 0.01))
    shared.to_dict()
    outcome = evaluate(shared, pairs(glory=2.0, win_rate=-0.02), CAND, CHAMPION)
    assert outcome.failures == ["guardrail_breach:win_rate"]
    assert outcome.per_metric["glory"]["primary"]["passed"]
    assert outcome.per_metric["glory"]["performance"]["passed"]
    assert not outcome.per_metric["win_rate"]["agent_guardrail"]["passed"]
    assert outcome.per_metric["win_rate"]["performance"]["passed"]


def test_overlapping_primary_is_not_overwritten_by_performance():
    shared = design(primary=("glory", "up", 1.0))
    outcome = evaluate(shared, pairs(glory=0.2, win_rate=-0.02), CAND, CHAMPION)
    assert "primary_not_significant:glory" in outcome.failures
    assert "performance_slip_without_significance:win_rate" in outcome.failures
