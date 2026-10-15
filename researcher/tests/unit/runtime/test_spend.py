"""Spend caps: configuration, and pricing a card from its Hermes sessions (runtime.spend)."""

import datetime

import pytest
from support.state_db import add_session, record_session_start

from autoresearch.queue.card import Card, CardSpec, RunInfo
from autoresearch.runctx import RunContext
from autoresearch.runtime import spend, state_db

PRICES = spend.DEFAULT_FALLBACK_PRICES


def test_defaults_and_env_overrides():
    assert spend.SpendConfig.from_env({}) == spend.SpendConfig(4, 4.0, 100.0, PRICES)
    config = spend.SpendConfig.from_env({
        "AUTORESEARCH_MAX_CARD_RUNS": "2",
        "AUTORESEARCH_MAX_CARD_USD": "1.5",
        "AUTORESEARCH_DAILY_BUDGET_USD": "0",
        "AUTORESEARCH_FALLBACK_PRICES": "output=20, cache_read=1",
    })
    assert (config.max_runs, config.max_card_usd, config.daily_usd) == (2, 1.5, 0.0)
    assert config.prices == PRICES | {"output": 20.0, "cache_read": 1.0}


@pytest.mark.parametrize("env", [
    {"AUTORESEARCH_MAX_CARD_RUNS": "2.5"},
    {"AUTORESEARCH_MAX_CARD_USD": "-1"},
    {"AUTORESEARCH_DAILY_BUDGET_USD": "lots"},
    {"AUTORESEARCH_FALLBACK_PRICES": "reasoning=3"},
    {"AUTORESEARCH_FALLBACK_PRICES": "input=cheap"},
])
def test_bad_values_are_refused(env):
    with pytest.raises(ValueError):
        spend.SpendConfig.from_env(env)


def test_runs_that_did_no_work_are_not_counted():
    spec = CardSpec("Q-1", "t", "investigation", "w", [], "x", "P2")
    runs = [RunInfo("1", True, "spawn_failed"), RunInfo("2", True, "rate_limited"),
            RunInfo("3", True, "blocked"), RunInfo("4", False, None)]
    assert spend.counted_runs(Card("t_1", spec, "claimed", "p", runs)) == 2


def test_a_card_costs_its_sessions_and_their_descendants(tmp_path):
    ctx = RunContext(tmp_path / "home", tmp_path / "ws", None, None, None)
    ctx.hermes_home.mkdir()
    record_session_start(ctx.canonical, "t_1", "1", "s1")
    record_session_start(ctx.canonical, "t_1", "2", "s2")
    record_session_start(ctx.canonical, "t_2", "3", "other")
    add_session(ctx.hermes_home, "s1", 1.25)
    add_session(ctx.hermes_home, "s1-compressed", 0.5, parent="s1")  # context compression
    add_session(ctx.hermes_home, "s1-subagent", 0.25, parent="s1-compressed")
    add_session(ctx.hermes_home, "s2", 0.0, actual=2.0, status="actual")  # actual wins
    add_session(ctx.hermes_home, "other", 9.0)
    cost = spend.card_cost(ctx, "t_1", PRICES)
    assert cost.usd == pytest.approx(4.0) and cost.unpriced == []
    assert spend.card_cost(ctx, "t_9", PRICES).usd == 0.0


def test_a_session_without_a_hermes_estimate_is_priced_from_its_tokens(tmp_path):
    add_session(tmp_path, "s1", 0.0, status="unknown", tokens=(1_000_000, 100_000, 2_000_000, 0))
    add_session(tmp_path, "free", 0.0, status="included", tokens=(5_000_000, 0, 0, 0))
    add_session(tmp_path, "empty", None, status=None)
    cost = spend.sessions_cost(tmp_path, {"s1", "free", "empty"}, PRICES)
    assert cost.usd == pytest.approx(3.0 + 1.2 + 1.5)
    assert cost.unpriced == ["s1"]


def test_day_cost_counts_sessions_started_this_utc_day(tmp_path):
    now = datetime.datetime(2026, 10, 9, 1, 0, tzinfo=datetime.UTC)
    midnight = datetime.datetime(2026, 10, 9, tzinfo=datetime.UTC).timestamp()
    add_session(tmp_path, "yesterday", 20.0, started_at=midnight - 60)
    add_session(tmp_path, "today", 3.0, started_at=midnight + 60)
    assert spend.day_cost(tmp_path, now, PRICES).usd == pytest.approx(3.0)


def test_no_state_db_yet_costs_nothing(tmp_path):
    assert spend.sessions_cost(tmp_path, {"s1"}, PRICES).usd == 0.0
    assert spend.day_cost(tmp_path, datetime.datetime.now(datetime.UTC), PRICES).usd == 0.0


def test_the_research_day_leaves_out_coach_chat_sessions(tmp_path):
    now = datetime.datetime(2026, 10, 9, 12, 0, tzinfo=datetime.UTC)
    midnight = datetime.datetime(2026, 10, 9, tzinfo=datetime.UTC).timestamp()
    add_session(tmp_path, "card", 2.0, started_at=midnight + 60, source="cli")
    add_session(tmp_path, "cron", 0.5, started_at=midnight + 60, source="cron")
    add_session(tmp_path, "chat", 9.0, started_at=midnight + 60, source="api_server")
    assert spend.day_cost(tmp_path, now, PRICES).usd == pytest.approx(2.5)


def _set_cost(home, session_id, usd):
    import sqlite3

    connection = sqlite3.connect(home / "state.db")
    connection.execute("UPDATE sessions SET estimated_cost_usd = ? WHERE id = ?", (usd, session_id))
    connection.commit()
    connection.close()


def test_chat_day_spend_is_the_rise_since_the_days_first_snapshot(tmp_path):
    snapshot = tmp_path / "local" / "chat-spend.json"
    day1 = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=datetime.UTC)
    midnight = datetime.datetime(2026, 10, 9, tzinfo=datetime.UTC).timestamp()
    add_session(tmp_path, "bridge", 1.0, started_at=midnight - 86400, source="api_server")
    add_session(tmp_path, "card", 7.0, started_at=midnight + 60, source="cli")
    assert state_db.chat_day_usd(tmp_path, snapshot, day1, PRICES) == 0.0  # yesterday's
    _set_cost(tmp_path, "bridge", 4.1)
    assert state_db.chat_day_usd(tmp_path, snapshot, day1, PRICES) == pytest.approx(3.1)
    add_session(tmp_path, "bridge-2", 0.25, started_at=midnight + 3600, source="api_server")
    assert state_db.chat_day_usd(tmp_path, snapshot, day1, PRICES) == pytest.approx(3.35)
    day2 = day1 + datetime.timedelta(days=1)  # a new UTC day starts from the totals then
    assert state_db.chat_day_usd(tmp_path, snapshot, day2, PRICES) == 0.0
    _set_cost(tmp_path, "bridge", 5.0)
    assert state_db.chat_day_usd(tmp_path, snapshot, day2, PRICES) == pytest.approx(0.9)


def test_chat_day_spend_without_a_state_db_is_zero(tmp_path):
    now = datetime.datetime.now(datetime.UTC)
    assert state_db.chat_day_usd(tmp_path, tmp_path / "snap.json", now, PRICES) == 0.0
