"""health (Plan 01 Task 3.9): blocked cards are requeued once, then repaired with a
help_request; low disk and credential expiry become one runtime help_request a day."""

import datetime

import pytest
from support.keeper_host import Host, Usage

from autoresearch.io.docs import read_json, write_json
from autoresearch.runtime import health

T0 = datetime.datetime.now(datetime.UTC)
GB = 1024**3


def at(minutes: float) -> datetime.datetime:
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.fixture
def host(tmp_path) -> Host:
    host = Host(tmp_path)
    host.tick(at(0))  # bootstrap + a planning request
    host.tick(at(1))  # the planning card is admitted
    return host


def _check(host: Host, now: datetime.datetime, free: int = 100 * GB) -> health.HealthRun:
    host.now = now
    return health.run(
        host.ctx, host.queue, host.sm, now, env=host.env, disk_free=lambda _p: Usage(0, 0, free)
    )


def _help(host: Host, text: str) -> list[dict]:
    return [m for m in host.outbox() if m["kind"] == "help_request" and text in m["title"]]


def _block(host: Host, task_id: str) -> None:
    path = host.queue.directory / "cards" / f"{task_id}.json"
    record = read_json(path)
    record["state"] = "blocked"
    write_json(path, record)


def test_healthy_host_reports_nothing(host):
    result = _check(host, at(2))
    assert result.problems == [] and result.blocked == [] and result.published == []


def test_blocked_card_is_requeued_once_then_repaired_with_one_help_request(host):
    [card] = host.queue.list("pending")
    _block(host, card.task_id)

    first = _check(host, at(2))
    assert [a.action for a in first.blocked] == ["requeued"]
    assert host.queue.get(card.task_id).state == "pending"
    assert _help(host, card.task_id) == []

    _block(host, card.task_id)
    second = _check(host, at(3))
    [action] = second.blocked
    assert action.action == "repaired"
    repair = host.receipt(action.repair_request)
    assert repair is None  # requested, admitted by the next queue-keeper run
    [request] = [r for r in host.requests() if r["id"] == action.repair_request]
    assert f"[repair-blocked:{card.task_id}]" in request["spec"]["why"]
    assert request["spec"]["mode"] == card.spec.mode
    [help_request] = _help(host, card.task_id)
    assert help_request["expects_response"] and help_request["priority"] == "P1"

    assert _check(host, at(4)).blocked == []  # nothing more for this card
    assert len(_help(host, card.task_id)) == 1
    assert host.remote_head() == host.head() and host.clean()


def test_low_disk_raises_one_help_request_a_day(host):
    first = _check(host, at(2), free=GB // 4)
    assert any("disk" in problem for problem in first.problems)
    assert len(_help(host, "disk")) == 1
    _check(host, at(3), free=GB // 4)
    assert len(_help(host, "disk")) == 1
    _check(host, at(2 + 24 * 60), free=GB // 4)
    assert len(_help(host, "disk")) == 2


def test_credential_expiring_soon_or_expired(host):
    host.set_faults("expires_in=3600")
    soon = _check(host, at(2))
    assert any("expires" in problem for problem in soon.problems)
    assert len(_help(host, "credential")) == 1

    host.set_faults("expires_in=-60")
    expired = _check(host, at(3))
    assert any("expired" in problem for problem in expired.problems)
    assert len(_help(host, "credential")) == 2


def test_whoami_failure_is_reported(host):
    host.set_faults("fail_after=whoami")
    result = _check(host, at(2))
    assert any("whoami" in problem for problem in result.problems)
    assert len(_help(host, "whoami")) == 1


def test_no_workspace_yet_is_not_an_error(tmp_path):
    host = Host(tmp_path)
    result = health.run(host.ctx, host.queue, host.sm, at(0), env=host.env)
    assert result.skipped and result.problems == []
