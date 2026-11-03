"""The inference state (JAME-174): the file, the gate, and every consumer waiting on it.

A spent key must stop chat, wakes and Researchers from starting runs, and work must resume on
its own once the lease loop installs a new key (or an outage block lapses).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from research_core import inference
from research_core.client import ScriptedClient
from research_core.serve import SeamServer
from research_core.watcher import dispatch, wakes
from research_core.watcher.hermes_api import FakeHermesRuns, RunsUnavailable
from tests.test_serve import make_ctx as make_seam_ctx
from tests.watcher._support import HARNESS, make_ctx, make_task

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
REFILL = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)


def lease(tmp_path, expires_at: datetime | None = None):
    lease_file = tmp_path / ".inference-lease"
    expires_at = expires_at or datetime.now(UTC) + timedelta(hours=4)
    lease_file.write_text(expires_at.isoformat())
    return lease_file


def block_budget(lease_file, until: datetime | None = None):
    until = until or datetime.now(UTC) + timedelta(hours=4)
    return inference.lease_refused(lease_file, until, budget_exhausted=True)


# --- the state file ----------------------------------------------------------------------------


def test_state_round_trips(tmp_path):
    lease_file = lease(tmp_path)
    written = inference.update_state(
        lease_file,
        lambda state: inference.InferenceState(
            blocked="provider_unavailable",
            until=NOW + timedelta(minutes=5),
            since=NOW,
            last_error=inference.ApiError("api_1", "overloaded", 503, NOW),
            next_refill_at=REFILL,
            outages=(NOW - timedelta(minutes=1), NOW),
            key_id="abc",
        ),
    )
    assert inference.read_state(lease_file) == written
    assert inference.state_path(lease_file) == tmp_path / ".inference-state"
    assert not (tmp_path / ".inference-state.pending").exists()


@pytest.mark.parametrize(
    "content",
    [
        None,
        "",
        "not json",
        "[]",
        '{"blocked": "budget_exhausted", "until": "tomorrow"}',
        '{"blocked": "budget_exhausted", "until": "2099-01-01T00:00:00"}',
    ],
)
def test_missing_or_malformed_state_is_not_blocked(tmp_path, content):
    lease_file = lease(tmp_path)
    if content is not None:
        inference.state_path(lease_file).write_text(content)
    assert inference.read_state(lease_file) == inference.InferenceState()
    assert inference.available(lease_file)


def test_budget_block_outlasts_the_refill_time_until_a_new_key(tmp_path):
    lease_file = lease(tmp_path, NOW + timedelta(days=1))
    inference.lease_installed(lease_file, REFILL + inference.RENEWAL_GRACE, "sk-one", now=NOW)
    inference.lease_refused(lease_file, REFILL, budget_exhausted=True, now=NOW)
    assert not inference.available(lease_file, now=NOW)
    assert inference.blocking_state(lease_file, now=NOW).until == REFILL

    # The refill time passes, but the spent key is still installed: the gate stays closed.
    after_refill = REFILL + timedelta(seconds=30)
    assert not inference.available(lease_file, now=after_refill)
    assert inference.blocking_state(lease_file, now=after_refill).blocked == "budget_exhausted"

    inference.lease_installed(lease_file, NOW + timedelta(days=2), "sk-two", now=after_refill)
    assert inference.available(lease_file, now=after_refill)


def test_outage_block_lapses_at_until(tmp_path):
    lease_file = lease(tmp_path, NOW + timedelta(days=1))
    until = NOW + timedelta(minutes=5)
    inference.update_state(
        lease_file, lambda state: state.with_block("provider_unavailable", until, NOW)
    )
    assert not inference.available(lease_file, now=NOW)
    assert inference.available(lease_file, now=until + timedelta(seconds=1))
    assert inference.blocking_state(lease_file, now=until + timedelta(seconds=1)) is None


def test_available_still_needs_an_unexpired_lease(tmp_path):
    assert inference.available(None)
    assert not inference.available(tmp_path / ".inference-lease")
    assert not inference.available(lease(tmp_path, datetime.now(UTC) - timedelta(seconds=1)))


def test_only_a_new_key_clears_a_budget_block(tmp_path):
    lease_file = lease(tmp_path)
    expires = REFILL + inference.RENEWAL_GRACE
    first = inference.lease_installed(lease_file, expires, "sk-one", now=NOW)
    assert first.next_refill_at == REFILL and first.blocked is None
    assert first.key_changed_at == NOW
    block_budget(lease_file)

    # The platform hands back the same, spent key until it nears expiry: still blocked.
    inference.lease_installed(lease_file, expires, "sk-one", now=NOW + timedelta(hours=1))
    same = inference.read_state(lease_file)
    assert same.blocked == "budget_exhausted" and same.key_changed_at == NOW

    cleared = inference.lease_installed(lease_file, expires + timedelta(days=1), "sk-two")
    assert cleared.blocked is None and cleared.until is None
    assert cleared.key_changed_at > NOW
    assert cleared.next_refill_at == REFILL + timedelta(days=1)
    assert "sk-two" not in inference.state_path(lease_file).read_text()


def test_next_refill_falls_back_to_midnight_utc():
    assert inference.next_refill(inference.InferenceState(), NOW) == REFILL
    stale = inference.InferenceState(next_refill_at=NOW - timedelta(hours=1))
    assert inference.next_refill(stale, NOW) == REFILL
    known = inference.InferenceState(next_refill_at=NOW + timedelta(hours=1))
    assert inference.next_refill(known, NOW) == NOW + timedelta(hours=1)


# --- the gate in HermesRuns ------------------------------------------------------------------


def test_start_refuses_with_the_reason_while_blocked(tmp_path):
    lease_file = lease(tmp_path)
    hermes = FakeHermesRuns(inference_lease_file=lease_file)
    block_budget(lease_file)
    with pytest.raises(RunsUnavailable) as info:
        hermes.start(profile="orchestrator", session_id="s", text="hi")
    assert info.value.code == "inference_unavailable"
    assert "budget_exhausted until" in info.value.detail and hermes.runs == {}


# --- every consumer waits, then resumes --------------------------------------------------------


def test_chat_message_waits_while_blocked_and_starts_after_the_state_clears(tmp_path):
    lease_file = lease(tmp_path)
    block_budget(lease_file)
    ctx = make_seam_ctx(tmp_path)
    hermes = FakeHermesRuns(inference_lease_file=lease_file)
    server = SeamServer(ws=ctx.workspace, hermes=hermes, inference_lease_file=lease_file)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = ScriptedClient(server.url)
        client.open()
        receipt = client.say("what is the champion?")
        assert receipt["delivered"] == "queued"
        with pytest.raises(TimeoutError):
            client.wait_admission(receipt["op_id"], timeout=0.7)
        assert hermes.runs == {}

        inference.lease_installed(lease_file, datetime.now(UTC) + timedelta(days=1), "sk-new")
        assert client.wait_admission(receipt["op_id"])["status"] == "running"
        assert len(hermes.runs) == 1
    finally:
        server.shutdown()


def test_wake_defers_without_using_an_attempt_and_starts_after_the_state_clears(tmp_path):
    lease_file = lease(tmp_path)
    block_budget(lease_file)
    ctx = make_ctx(tmp_path, hermes=FakeHermesRuns(inference_lease_file=lease_file))
    with ctx.store.tx(HARNESS) as tx:
        seq = tx.event("task.asked", "T-1", {})

    report = wakes.run(ctx)
    row = ctx.store.one("SELECT * FROM handled_events WHERE seq = ?", (seq,))
    assert report["busy"] == [f"cow_fake:{seq}"] and report["started"] == []
    assert row["attempts"] == 1 and ctx.hermes.runs == {}
    wakes.run(ctx)
    assert ctx.store.one("SELECT * FROM handled_events WHERE seq = ?", (seq,))["attempts"] == 1

    inference.lease_installed(lease_file, datetime.now(UTC) + timedelta(days=1), "sk-new")
    report = wakes.run(ctx)
    assert len(report["started"]) == 1 and len(ctx.hermes.runs) == 1
    assert ctx.store.one("SELECT * FROM handled_events WHERE seq = ?", (seq,))["attempts"] == 1


def test_researcher_dispatch_defers_without_using_an_attempt_until_the_block_lapses(tmp_path):
    lease_file = lease(tmp_path)
    inference.update_state(
        lease_file,
        lambda state: state.with_block(
            "provider_unavailable", datetime.now(UTC) + timedelta(seconds=60), datetime.now(UTC)
        ),
    )
    ctx = make_ctx(tmp_path, hermes=FakeHermesRuns(inference_lease_file=lease_file))
    make_task(ctx)

    blocked = dispatch.run(ctx)
    assert blocked["started"] == [] and "provider_unavailable" in blocked["deferred"]
    assert ctx.hermes.runs == {}

    # The outage block is never cleared; it lapses at until.
    inference.update_state(
        lease_file,
        lambda state: state.with_block(
            "provider_unavailable", datetime.now(UTC) - timedelta(seconds=1), datetime.now(UTC)
        ),
    )
    later = dispatch.run(ctx)
    [started] = later["started"]
    assert ctx.hermes.runs[started["run_id"]].idempotency_key == "task:1:0:1"
