"""Credits: the balance read, the cost estimate and its fallbacks, the one-line description."""

from pathlib import Path

from research_core.platform import credits
from research_core.platform.fake import FakePlatform
from research_core.platform.protocol import Credits


def recent(episodes: int, cost: float) -> dict:
    return {"experience_request_id": "xreq_x", "episodes": episodes, "compute_cost_credits": cost}


def test_read_returns_the_platform_balance(tmp_path: Path):
    platform = FakePlatform(tmp_path / "fake.json", {"IDE_FAKE_FAULTS": "credits=42"})
    found = credits.read(platform)
    assert isinstance(found, Credits) and found.balance == 42.0


def test_estimate_uses_recent_requests_weighted_by_episodes():
    history = Credits(balance=10, recent_requests=[recent(2, 1.0), recent(4, 1.0)])
    # 2 credits over 6 episodes: a third of a credit per episode.
    assert credits.estimate(6, credits=history, cost_per_episode=5.0) == 2.0
    assert credits.recent_cost_per_episode(history) == 2.0 / 6


def test_estimate_falls_back_to_the_mixin_cost_per_episode():
    empty = Credits(balance=10, recent_requests=[])
    assert credits.estimate(4, credits=empty, cost_per_episode=0.25) == 1.0
    assert credits.estimate(4, credits=None, cost_per_episode=0.25) == 1.0


def test_estimate_is_none_without_history_or_a_mixin_cost():
    assert credits.estimate(4, credits=Credits(balance=10), cost_per_episode=None) is None
    assert credits.estimate(4, credits=None, cost_per_episode=None) is None


def test_estimate_skips_rows_that_report_no_usable_numbers():
    history = Credits(
        balance=10,
        recent_requests=[
            {"episodes": 0, "compute_cost_credits": 3.0},
            {"episodes": "2", "compute_cost_credits": 3.0},
            {"episodes": 2},
            recent(2, 1.0),
        ],
    )
    assert credits.estimate(2, credits=history, cost_per_episode=None) == 1.0
    only_bad = Credits(balance=10, recent_requests=[{"episodes": 0, "compute_cost_credits": 3.0}])
    assert credits.estimate(2, credits=only_bad, cost_per_episode=0.5) == 1.0


def test_describe_is_one_line():
    full = Credits(
        balance=99.0,
        max=100.0,
        refill_amount=100.0,
        next_refill_at="2026-10-29T00:00:00+00:00",
        spent_this_period=1.0,
    )
    line = credits.describe(full)
    assert "\n" not in line
    assert line == (
        "credits: 99 available of 100; 1 spent this period;"
        " next refill 100 at 2026-10-29T00:00:00+00:00"
    )
    assert credits.describe(Credits(balance=3.5)) == "credits: 3.5 available"
    assert credits.describe(None) == "credits: unknown (the platform did not answer)"
