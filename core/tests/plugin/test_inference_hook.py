"""The ``api_request_error`` hook records failed model calls in the inference state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_core import inference
from research_core.plugin import register
from research_core.plugin.inference_hook import make_api_request_error, record_api_error

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
REFILL = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)


@pytest.fixture
def lease_file(tmp_path):
    return tmp_path / ".inference-lease"


def record(lease_file, reason, *, at=NOW, status_code=None, message="", session_id="api_1"):
    return record_api_error(
        lease_file,
        session_id=session_id,
        reason=reason,
        status_code=status_code,
        message=message,
        now=at,
    )


def test_billing_blocks_until_the_recorded_refill(lease_file):
    inference.lease_installed(lease_file, REFILL + inference.RENEWAL_GRACE, "sk-one", now=NOW)
    state = record(lease_file, "billing", status_code=402)
    assert state.blocked == "budget_exhausted" and state.until == REFILL and state.since == NOW
    assert state.last_error == inference.ApiError("api_1", "billing", 402, NOW)
    assert inference.read_state(lease_file) == state


def test_billing_without_a_known_refill_blocks_until_midnight_utc(lease_file):
    assert record(lease_file, "billing").until == REFILL


def test_key_limit_403_counts_as_billing(lease_file):
    state = record(lease_file, "auth", status_code=403, message="Key limit exceeded (total limit)")
    assert state.blocked == "budget_exhausted"
    assert state.last_error.reason == "billing"


def test_outages_block_only_after_three_within_the_window(lease_file):
    first = record(lease_file, "overloaded", status_code=503)
    second = record(lease_file, "server_error", at=NOW + timedelta(seconds=30), status_code=500)
    assert first.blocked is None and second.blocked is None
    third = record(lease_file, "timeout", at=NOW + timedelta(seconds=60))
    assert third.blocked == "provider_unavailable"
    assert third.until == NOW + timedelta(seconds=60) + timedelta(minutes=5)


def test_spread_out_outages_do_not_block(lease_file):
    for minutes in (0, 4, 8, 12):
        state = record(lease_file, "overloaded", at=NOW + timedelta(minutes=minutes))
    assert state.blocked is None and len(state.outages) == 1


def test_other_classes_only_record_last_error(lease_file):
    for index, reason in enumerate(("rate_limit", "context_overflow", "rate_limit", "unknown")):
        state = record(lease_file, reason, at=NOW + timedelta(seconds=index), session_id=reason)
    assert state.blocked is None and state.outages == ()
    last = inference.ApiError("unknown", "unknown", None, NOW + timedelta(seconds=3))
    assert state.last_error == last


def test_an_outage_does_not_override_a_budget_block(lease_file):
    record(lease_file, "billing")
    for seconds in (1, 2, 3):
        state = record(lease_file, "overloaded", at=NOW + timedelta(seconds=seconds))
    assert state.blocked == "budget_exhausted" and state.until == REFILL
    assert state.last_error.reason == "overloaded"


def test_hook_reads_the_lease_path_from_the_environment(lease_file, monkeypatch):
    monkeypatch.setenv("IDE_INFERENCE_LEASE_FILE", str(lease_file))
    hook = make_api_request_error()
    assert (
        hook(
            session_id="api_9",
            reason="billing",
            status_code=402,
            error={"type": "APIStatusError", "message": "Insufficient credits"},
            task_id="t",
            telemetry_schema_version=1,
        )
        is None
    )
    state = inference.read_state(lease_file)
    assert state.blocked == "budget_exhausted" and state.last_error.session_id == "api_9"


def test_hook_does_nothing_without_a_lease_file_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("IDE_INFERENCE_LEASE_FILE", raising=False)
    hook = make_api_request_error()
    hook(session_id="api_1", reason="billing")
    assert list(tmp_path.iterdir()) == []

    (tmp_path / "a-file").write_text("")
    monkeypatch.setenv("IDE_INFERENCE_LEASE_FILE", str(tmp_path / "a-file" / ".inference-lease"))
    hook(session_id="api_1", reason="billing", status_code="402", error="not a dict")


class HookContext:
    def __init__(self):
        self.hooks = []

    def register_tool(self, **kwargs):
        pass

    def register_hook(self, name, callback):
        self.hooks.append(name)

    def register_system_prompt_section(self, *args, **kwargs):
        pass


def test_both_profiles_register_the_hook():
    for role in ("orchestrator", "researcher"):
        ctx = HookContext()
        register.make_register(role)(ctx)
        assert "api_request_error" in ctx.hooks
