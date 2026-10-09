"""Spend caps on the fake host (runtime.spend): queue-keeper stops a card over its run or
estimated-cost cap with one help_request and never requeues it; health holds a blocked card
at the run cap; today's spend at the daily budget pauses dispatch until the next UTC day."""

import datetime

import pytest
from support.keeper_host import PROFILE, Host
from support.state_db import add_session, record_session_start

from autoresearch.io.docs import read_json, write_json
from autoresearch.runtime import health, spend
from autoresearch.runtime.pause import is_paused

T0 = datetime.datetime.now(datetime.UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture
def host(tmp_path) -> Host:
    host = Host(tmp_path)
    host.tick(at(0))  # bootstrap + a planning request
    host.tick(at(1))  # the planning card is admitted
    return host


def _card(host):
    [card] = host.queue.list()
    return card


def _runs(host, count: int, *, end_claimed: bool):
    """Give the card ``count`` runs (each claimed, then returned to pending by a reclaim);
    the last one stays claimed when ``end_claimed``."""
    task_id = _card(host).task_id
    for number in range(count):
        claimed = host.queue.claim_next(PROFILE)
        assert claimed.task_id == task_id
        if number < count - 1 or not end_claimed:
            host.queue.requeue(task_id, "reclaimed")
    return task_id


def _cap_help(host) -> list[dict]:
    return [m for m in host.outbox() if m["title"].startswith("Card stopped at its spend cap")]


def test_a_card_that_starts_run_five_is_stopped_once_and_never_requeued(host):
    task_id = _runs(host, 5, end_claimed=True)
    result = host.tick(at(2))

    card = host.queue.get(task_id)
    assert card.state == "blocked" and card.block_reason.startswith("spend-cap: it started run 5")
    [stopped] = result.spend_run.stopped
    assert stopped[0] == task_id
    [help_request] = _cap_help(host)
    assert help_request["expects_response"] and help_request["about"]["card"] == task_id
    marker = host.committed(spend.marker_path(host.canonical, task_id))
    assert marker["stops"][0]["runs"] == 5

    host.tick(at(3))
    host.now = at(4)
    health.run(host.ctx, host.queue, host.sm, at(4), env=host.env)
    assert host.queue.get(task_id).state == "blocked"  # no requeue, no repair
    assert len(_cap_help(host)) == 1
    assert [r for r in host.requests() if "repair-blocked" in r["spec"]["why"]] == []


def test_a_card_at_four_runs_is_not_dispatched_again(host):
    task_id = _runs(host, 4, end_claimed=False)
    assert host.queue.get(task_id).state == "pending"
    host.tick(at(2))
    card = host.queue.get(task_id)
    assert card.state == "blocked" and "already had 4 runs" in card.block_reason


def test_the_fourth_run_itself_may_run(host):
    task_id = _runs(host, 4, end_claimed=True)
    host.tick(at(2))
    assert host.queue.get(task_id).state == "claimed"
    assert _cap_help(host) == []


def test_a_card_over_its_estimated_cost_is_stopped(host):
    task_id = _runs(host, 1, end_claimed=True)
    record_session_start(host.canonical, task_id, "r-1", "s1")
    add_session(host.home, "s1", 3.0)
    host.tick(at(2))
    assert host.queue.get(task_id).state == "claimed"  # $3.00 of $4.00

    add_session(host.home, "s1-compressed", 1.25, parent="s1")
    host.tick(at(3))
    card = host.queue.get(task_id)
    assert card.state == "blocked" and "$4.25" in card.block_reason
    [help_request] = _cap_help(host)
    assert "$4.25" in help_request["body"] and "$4.00" in help_request["body"]


def test_unblocking_a_stopped_card_gives_it_another_allowance(host):
    task_id = _runs(host, 5, end_claimed=True)
    host.tick(at(2))
    host.queue.requeue(task_id, "a human unblocked it")
    host.tick(at(3))
    assert host.queue.get(task_id).state == "pending"
    host.queue.claim_next(PROFILE)  # run 6, the first of the new allowance
    host.tick(at(4))
    assert host.queue.get(task_id).state == "claimed"


def test_health_holds_a_blocked_card_at_the_run_cap_instead_of_requeueing(host):
    task_id = _runs(host, 4, end_claimed=True)
    host.queue.block(task_id, host.queue.get(task_id).runs[-1].run_id, "circuit breaker open")
    host.now = at(2)
    result = health.run(host.ctx, host.queue, host.sm, at(2), env=host.env)
    assert [(a.task_id, a.action) for a in result.blocked] == [(task_id, "spend-cap")]
    assert host.queue.get(task_id).state == "blocked"
    [help_request] = _cap_help(host)
    assert "kept the blocked card" in help_request["body"]
    assert health.run(host.ctx, host.queue, host.sm, at(3), env=host.env).blocked == []
    host.tick(at(4))
    assert host.queue.get(task_id).state == "blocked" and len(_cap_help(host)) == 1


def test_a_blocked_card_under_the_cap_is_still_requeued(host):
    task_id = _runs(host, 1, end_claimed=True)
    host.queue.block(task_id, host.queue.get(task_id).runs[-1].run_id, "circuit breaker open")
    host.tick(at(2))
    assert host.queue.get(task_id).state == "pending"


def test_daily_budget_pauses_until_the_next_utc_day(host):
    task_id = _card(host).task_id
    add_session(host.home, "today", spend.DEFAULT_DAILY_USD + 1, started_at=at(1).timestamp())
    first = host.tick(at(2))
    assert first.spend_run.over_budget and first.spend_run.paused
    assert is_paused(host.ledger) and host.queue.get(task_id).assignee is None
    budget = [m for m in host.outbox() if m["title"] == "Agent paused: daily spend budget reached"]
    assert len(budget) == 1 and budget[0]["priority"] == "P0"

    second = host.tick(at(3))  # preflight passes, but the budget holds the pause
    assert second.resumed is None and is_paused(host.ledger)
    assert len([m for m in host.outbox() if "daily spend budget" in m["title"]]) == 1

    next_day = T0 + datetime.timedelta(days=1)
    third = host.tick(next_day)
    assert third.resumed == [task_id] and not is_paused(host.ledger)
    assert host.queue.get(task_id).assignee == PROFILE


def test_a_budget_pause_is_kept_when_the_spend_cannot_be_read(host):
    add_session(host.home, "today", spend.DEFAULT_DAILY_USD + 1, started_at=at(1).timestamp())
    host.tick(at(2))
    host.env["AUTORESEARCH_MAX_CARD_RUNS"] = "bad"  # the spend step fails
    result = host.tick(at(3))
    assert "spend" in result.failures and result.resumed is None and is_paused(host.ledger)


def test_a_preflight_pause_is_not_resumed_while_over_budget(host):
    path = host.ledger / "pause.json"
    host.tick(at(2))
    write_json(path, {"reason": "preflight failed: x", "paused_at": "t", "tasks": {}})
    add_session(host.home, "today", spend.DEFAULT_DAILY_USD + 5, started_at=at(1).timestamp())
    result = host.tick(at(3))
    assert result.resumed is None and read_json(path)["reason"] == "preflight failed: x"


def test_a_blocked_card_over_its_cost_cap_is_held_not_requeued(host):
    task_id = _runs(host, 2, end_claimed=True)
    record_session_start(host.canonical, task_id, "r-1", "s1")
    add_session(host.home, "s1", 5.0)
    host.queue.block(task_id, host.queue.get(task_id).runs[-1].run_id, "circuit breaker open")
    result = host.tick(at(2))
    assert [(a.task_id, a.action) for a in result.blocked] == [(task_id, "spend-cap")]
    assert host.queue.get(task_id).state == "blocked"
    [help_request] = _cap_help(host)
    assert "$5.00" in help_request["body"]


def test_a_card_blocked_again_after_its_requeue_at_the_run_cap_is_held_not_repaired(host):
    task_id = _runs(host, 3, end_claimed=True)
    host.queue.block(task_id, host.queue.get(task_id).runs[-1].run_id, "circuit breaker open")
    host.tick(at(2))  # requeued once (marker: requeued)
    assert host.queue.get(task_id).state == "pending"
    run = host.queue.claim_next(PROFILE).runs[-1].run_id  # run 4
    host.queue.block(task_id, run, "circuit breaker open")
    result = host.tick(at(3))
    assert [(a.task_id, a.action) for a in result.blocked] == [(task_id, "spend-cap")]
    assert [r for r in host.requests() if "repair-blocked" in r["spec"]["why"]] == []


def test_caps_still_apply_while_preflight_fails(host):
    task_id = _runs(host, 1, end_claimed=True)
    record_session_start(host.canonical, task_id, "r-1", "s1")
    add_session(host.home, "s1", 4.5)
    host.set_faults("player=ply_someone_else")
    result = host.tick(at(2))
    assert result.paused and host.queue.get(task_id).state == "blocked"
    assert len(_cap_help(host)) == 1


def test_caps_still_apply_when_a_whoami_blip_skips_the_run(host):
    """A skipped preflight (whoami unavailable) does not pause, but running cards keep
    running, so the spend step still stops a card over its cap."""
    task_id = _runs(host, 1, end_claimed=True)
    record_session_start(host.canonical, task_id, "r-1", "s1")
    add_session(host.home, "s1", 4.5)
    host.set_faults("fail_after=whoami")
    result = host.tick(at(2))
    assert result.preflight.skipped and not result.paused
    assert host.queue.get(task_id).state == "blocked"
    assert len(_cap_help(host)) == 1


def test_a_stop_that_fails_is_retried_every_minute_and_reported_once(host, monkeypatch):
    """A failed stop grants no new allowance: the runtime retries until the stop works."""
    task_id = _runs(host, 5, end_claimed=True)
    real = host.queue.stop
    calls = []

    def flaky(task, reason):
        calls.append(task)
        if len(calls) < 3:
            raise RuntimeError("database is locked")
        return real(task, reason)

    monkeypatch.setattr(host.queue, "stop", flaky)
    first = host.tick(at(2))
    assert f"spend {task_id}" not in first.failures  # retried, so only logged
    assert host.queue.get(task_id).state == "claimed"
    host.tick(at(3))
    host.tick(at(4))  # the third attempt works
    assert calls == [task_id] * 3
    assert host.queue.get(task_id).state == "blocked"
    [not_stopped] = [m for m in host.outbox() if m["title"].startswith("Card not stopped")]
    assert "keeps trying" in not_stopped["body"]
    assert len(_cap_help(host)) == 1  # then the stop itself
    marker = host.committed(spend.marker_path(host.canonical, task_id))
    assert [stop["outcome"] for stop in marker["stops"]] == ["not_stopped", "stopped"]
    host.tick(at(5))
    assert calls == [task_id] * 3


def test_an_unreadable_state_db_neither_requeues_nor_holds_a_blocked_card(host):
    task_id = _runs(host, 1, end_claimed=True)
    record_session_start(host.canonical, task_id, "r-1", "s1")  # so its cost needs state.db
    host.queue.block(task_id, host.queue.get(task_id).runs[-1].run_id, "circuit breaker open")
    (host.home / "state.db").write_bytes(b"not a database")
    result = host.tick(at(2))
    assert result.blocked == [] and host.queue.get(task_id).state == "blocked"
    assert "blocked" not in result.failures


def test_a_stop_hermes_sends_to_triage_says_so(host, monkeypatch):
    _runs(host, 5, end_claimed=True)
    real = host.queue.stop
    monkeypatch.setattr(host.queue, "stop", lambda task, why: real(task, why) and "triage")
    host.tick(at(2))
    [help_request] = _cap_help(host)
    assert "triage" in help_request["body"] and "unblock the card" not in help_request["body"]


def test_cards_hermes_cannot_block_are_skipped(host, monkeypatch):
    """triage, todo, scheduled, review: not dispatched, and `kanban block` refuses them."""
    task_id = _runs(host, 4, end_claimed=False)
    real_list = host.queue.list

    def listed(state=None):
        cards = real_list(state)
        for card in cards:
            card.board_status = "triage"
        return cards

    monkeypatch.setattr(host.queue, "list", listed)
    result = host.tick(at(2))
    assert result.spend_run.stopped == [] and "spend " + task_id not in result.failures
    assert host.queue.get(task_id).state == "pending"


def test_a_budget_reached_today_holds_the_pause_when_state_db_is_unreadable(host):
    path = host.ledger / "pause.json"
    host.tick(at(2))
    write_json(path, {"reason": "preflight failed: x", "paused_at": "t", "tasks": {}})
    add_session(host.home, "today", spend.DEFAULT_DAILY_USD + 5, started_at=at(1).timestamp())
    host.tick(at(3))  # over budget: hold recorded; preflight reason kept
    (host.home / "state.db").write_bytes(b"not a database")
    result = host.tick(at(4))
    assert "spend_db" in result.failures and result.resumed is None and is_paused(host.ledger)


def test_a_stop_that_cannot_be_recorded_does_not_end_enforcement(host, monkeypatch):
    """Codex review of PR #24, F2: recording a stop is a canonical commit, which a broken
    provenance journal fails; the card is still stopped and the spend step still returns
    (the daily budget is still checked)."""
    task_id = _runs(host, 5, end_claimed=True)

    def broken(*_args, **_kwargs):
        raise PermissionError("provenance journal unreadable")

    monkeypatch.setattr(spend, "_report", broken)
    result = host.tick(at(2))
    assert host.queue.get(task_id).state == "blocked"
    assert result.spend_run is not None
    assert f"spend record {task_id}" in result.failures
