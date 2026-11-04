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
from research_core.watcher.hermes_api import FakeHermesRuns, RunsUnavailable
from tests.test_serve import make_ctx as make_seam_ctx
from tests.watcher._support import HARNESS, coordinator, dispatch_inputs, make_ctx, make_task

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
REFILL = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)


def lease(tmp_path, expires_at: datetime | None = None):
    """A bare-ISO lease file: an expiry with no key known (``read_lease`` accepts it)."""
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
            blocked_key_id="abc",
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


def test_lease_file_records_the_installed_key(tmp_path):
    lease_file = tmp_path / ".inference-lease"
    expires = datetime.now(UTC) + timedelta(hours=4)
    installed = inference.lease_installed(lease_file, expires, "sk-one", now=NOW)
    assert installed == inference.InstalledLease(expires, inference.key_id("sk-one"), NOW)
    assert inference.read_lease(lease_file) == installed
    assert "sk-one" not in lease_file.read_text()
    assert not (tmp_path / ".inference-lease.pending").exists()
    assert not inference.state_path(lease_file).exists()
    assert inference.available(lease_file)


def test_spent_key_stays_blocked_when_the_same_key_is_reinstalled(tmp_path):
    lease_file = tmp_path / ".inference-lease"
    expires = datetime.now(UTC) + timedelta(hours=4)
    inference.lease_installed(lease_file, expires, "sk-one", now=NOW)
    block = block_budget(lease_file)
    assert block.blocked_key_id == inference.key_id("sk-one")

    # The platform hands back the same, spent key until it nears expiry: still blocked, and
    # the key keeps its original change time.
    again = inference.lease_installed(lease_file, expires, "sk-one", now=NOW + timedelta(hours=1))
    assert again.key_changed_at == NOW
    assert not inference.available(lease_file)
    assert inference.blocking_state(lease_file).blocked == "budget_exhausted"


def test_a_new_key_ends_a_budget_block_without_writing_the_state(tmp_path):
    lease_file = tmp_path / ".inference-lease"
    expires = datetime.now(UTC) + timedelta(hours=4)
    inference.lease_installed(lease_file, expires, "sk-one", now=NOW)
    block_budget(lease_file)
    state_file = inference.state_path(lease_file)
    before = (state_file.read_text(), state_file.stat().st_mtime_ns)
    state_file.chmod(0o444)  # any write to the state would fail

    new = inference.lease_installed(lease_file, expires + timedelta(days=1), "sk-two")
    assert new.key_changed_at > NOW
    assert inference.available(lease_file)
    assert inference.blocking_state(lease_file) is None
    assert (state_file.read_text(), state_file.stat().st_mtime_ns) == before
    # The state still names the spent key; it no longer matches the installed one.
    assert inference.read_state(lease_file).blocked_key_id == inference.key_id("sk-one")


def test_bare_iso_lease_reads_as_an_expiry_with_no_key(tmp_path):
    expires = datetime.now(UTC) + timedelta(hours=4)
    lease_file = lease(tmp_path, expires)
    assert inference.read_lease(lease_file) == inference.InstalledLease(expires)
    assert inference.available(lease_file)

    # A budget block with no key known holds while no key is known (None equals None) ...
    assert block_budget(lease_file).blocked_key_id is None
    assert not inference.available(lease_file)
    # ... and ends once a key is installed.
    installed = inference.lease_installed(lease_file, expires, "sk-one", now=NOW)
    assert installed.key_changed_at == NOW
    assert inference.available(lease_file)


@pytest.mark.parametrize(
    "content",
    ["", "not a time", "[]", '{"key_id": "abc"}', '{"expires_at": "2099-01-01T00:00:00"}'],
)
def test_malformed_lease_is_unavailable(tmp_path, content):
    lease_file = tmp_path / ".inference-lease"
    lease_file.write_text(content)
    assert inference.read_lease(lease_file) is None
    assert not inference.available(lease_file)


def test_next_refill_prefers_the_lease_then_the_refusal_then_midnight_utc():
    empty = inference.InferenceState()
    assert inference.next_refill(None, empty, NOW) == REFILL
    stale = inference.InferenceState(next_refill_at=NOW - timedelta(hours=1))
    assert inference.next_refill(None, stale, NOW) == REFILL
    known = inference.InferenceState(next_refill_at=NOW + timedelta(hours=1))
    assert inference.next_refill(None, known, NOW) == NOW + timedelta(hours=1)
    current = inference.InstalledLease(NOW + timedelta(hours=2) + inference.RENEWAL_GRACE)
    assert inference.next_refill(current, known, NOW) == NOW + timedelta(hours=2)
    expired = inference.InstalledLease(NOW)
    assert inference.next_refill(expired, known, NOW) == NOW + timedelta(hours=1)


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
    server = SeamServer(
        ws=ctx.workspace, hermes=hermes, inference_lease_file=lease_file, env=dict(ctx.env)
    )
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

    owner = coordinator(ctx)
    owner.advance_events(ctx)
    dispatch_inputs(ctx)
    row = ctx.store.one("SELECT * FROM handled_events WHERE seq=?", (seq,))
    assert row["attempts"] == 1 and ctx.hermes.runs == {}
    owner.advance_events(ctx)
    dispatch_inputs(ctx)
    assert ctx.store.one("SELECT * FROM handled_events WHERE seq=?", (seq,))["attempts"] == 1
    [queued] = ctx.wstore.q("SELECT * FROM client_admissions")
    assert queued["dispatch_started"] == 0

    inference.lease_installed(lease_file, datetime.now(UTC) + timedelta(days=1), "sk-new")
    dispatch_inputs(ctx)
    assert len(ctx.hermes.runs) == 1
    assert ctx.store.one("SELECT * FROM handled_events WHERE seq=?", (seq,))["attempts"] == 1


def test_researcher_dispatch_defers_without_using_an_attempt_until_the_block_lapses(tmp_path):
    lease_file = lease(tmp_path)
    inference.update_state(
        lease_file,
        lambda state: state.with_block(
            "provider_unavailable", datetime.now(UTC) + timedelta(seconds=60), datetime.now(UTC)
        ),
    )
    ctx = make_ctx(tmp_path, hermes=FakeHermesRuns(inference_lease_file=lease_file))
    task = make_task(ctx)
    from research_core.coordinator import Coordinator
    from research_core.tools.base import ToolContext

    command = ToolContext.from_env("orchestrator", {"IDE_WORKSPACE": str(ctx.ws.root)})
    reserved = Coordinator.reserve(
        command,
        task_id=1,
        task_updated=task["updated"],
        profile="researcher",
        assignment_id="blocked-worker",
        context="Work",
        rationale="Fit",
    )
    dispatch_inputs(ctx)
    assert ctx.hermes.runs == {}
    [queued] = ctx.wstore.q("SELECT * FROM client_admissions")
    assert queued["dispatch_started"] == 0
    # The outage block is never cleared; it lapses at until.
    inference.update_state(
        lease_file,
        lambda state: state.with_block(
            "provider_unavailable", datetime.now(UTC) - timedelta(seconds=1), datetime.now(UTC)
        ),
    )
    dispatch_inputs(ctx)
    [started] = ctx.hermes.runs.values()
    assert started.session_id == reserved["session_id"]
    assert started.idempotency_key == queued["native_key"]
