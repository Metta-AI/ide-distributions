"""The ``api_request_error`` hook: record failed model calls in the inference state.

Hermes calls the hook after it classifies each failed model call, with the class as ``reason``
(a ``FailoverReason`` value from ``agent/error_classifier.py``: ``billing``, ``rate_limit``,
``overloaded``, ``server_error``, ``timeout``, ...), plus ``session_id`` and ``status_code``.
The hook always records the call as ``last_error``, then:

* on ``billing`` (402 credits, or a 403 "Key limit exceeded") it blocks model work as
  ``budget_exhausted`` for the installed key (the lease file's ``key_id``), showing the next
  refill as ``until``. The block ends when the lease loop installs a different key; nobody
  writes to clear it. Within :data:`KEY_CHANGE_GRACE` of the lease's ``key_changed_at`` it
  does not block: such errors come from runs still finishing on the previous, spent key.
* after :data:`OUTAGE_COUNT` outage-class errors within :data:`OUTAGE_WINDOW` it blocks model
  work as ``provider_unavailable`` for :data:`OUTAGE_BLOCK`. Nobody clears that block; it lapses.

The state file lives beside ``IDE_INFERENCE_LEASE_FILE``; without that variable (a profile
run outside a platform-managed workspace) the hook does nothing. It runs inside Hermes: stdlib
only, and it never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from research_core import inference
from research_core.plugin.executor import profile_env

log = logging.getLogger(__name__)

OUTAGE_REASONS = frozenset({"overloaded", "server_error", "timeout"})
OUTAGE_COUNT = 3
OUTAGE_WINDOW = timedelta(minutes=3)
OUTAGE_BLOCK = timedelta(minutes=5)
KEY_CHANGE_GRACE = timedelta(minutes=2)


def is_billing(reason: str, status_code: int | None, message: str) -> bool:
    """Hermes's ``billing`` class, or OpenRouter's 403 "Key limit exceeded" in case Hermes's
    classifier ever words it differently."""
    return reason == "billing" or (status_code == 403 and "key limit exceeded" in message.lower())


def record_api_error(
    lease_file: Path,
    *,
    session_id: str,
    reason: str,
    status_code: int | None,
    message: str = "",
    now: datetime | None = None,
) -> inference.InferenceState:
    """Record one failed model call in the inference state and set the block it implies."""
    now = now or datetime.now(UTC)
    billing = is_billing(reason, status_code, message)
    lease = inference.read_lease(lease_file)
    installed_key_id = None if lease is None else lease.key_id

    def change(state: inference.InferenceState) -> inference.InferenceState:
        state = replace(
            state,
            last_error=inference.ApiError(
                session_id=session_id,
                reason="billing" if billing else reason,
                status_code=status_code,
                at=now,
            ),
        )
        if billing:
            changed_at = None if lease is None else lease.key_changed_at
            if changed_at is not None and now - changed_at < KEY_CHANGE_GRACE:
                return state
            return state.with_block(
                "budget_exhausted",
                inference.next_refill(lease, state, now),
                now,
                installed_key_id,
            )
        if reason not in OUTAGE_REASONS:
            return state
        outages = tuple(at for at in state.outages if now - at < OUTAGE_WINDOW) + (now,)
        state = replace(state, outages=outages)
        # A budget block in force outranks an outage: the refill, not the provider, ends it.
        if len(outages) >= OUTAGE_COUNT and not (
            state.blocked == "budget_exhausted"
            and state.blocking(now, installed_key_id=installed_key_id)
        ):
            state = state.with_block("provider_unavailable", now + OUTAGE_BLOCK, now)
        return state

    return inference.update_state(lease_file, change)


def make_api_request_error() -> Callable[..., None]:
    def api_request_error(
        session_id: str = "",
        reason: str | None = None,
        status_code: int | None = None,
        error: Any = None,
        **_: Any,
    ) -> None:
        try:
            lease_file = profile_env().get("IDE_INFERENCE_LEASE_FILE")
            if not lease_file:
                return
            message = str(error.get("message") or "") if isinstance(error, dict) else ""
            record_api_error(
                Path(lease_file),
                session_id=session_id or "",
                reason=reason or "unknown",
                status_code=status_code if isinstance(status_code, int) else None,
                message=message,
            )
        except Exception as failure:  # never take a model call down
            log.debug("inference-state hook failed: %s", failure)

    return api_request_error
