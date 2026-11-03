"""Whether model work may start: the platform's inference lease and the inference state.

Two files sit side by side in the folder of ``IDE_INFERENCE_LEASE_FILE``:

* the *lease file* holds the current key's expiry as an ISO time. The image's lease loop
  (``image/inference.py``) writes it whenever it installs a key.
* the *inference state* (``.inference-state``) is a small JSON object that says why model work
  is blocked and until when::

      {"blocked": "budget_exhausted" | "provider_unavailable" | null,
       "until": "<ISO time>" | null,
       "since": "<ISO time>",
       "last_error": {"session_id", "reason", "status_code", "at"} | null,
       "next_refill_at": "<ISO time>" | null,
       "outages": ["<ISO time>", ...],
       "key_id": "<sha256 prefix of the installed key>" | null,
       "key_changed_at": "<ISO time>" | null}

  Three processes share it, which is why it is a file and not a store table: the Hermes
  plugin's ``api_request_error`` hook (``research_core.plugin.inference_hook``) records each
  failed model call and sets the block; the image's lease loop records the next refill time,
  sets ``budget_exhausted`` when the platform refuses a lease and clears it when a new key
  arrives; and :func:`available` reads it before every run start. ``next_refill_at`` is the
  lease loop's best knowledge of the next refill (for the hook's ``until``); ``outages`` holds
  the times of recent outage-class errors, so the hook can count them across processes;
  ``key_id`` fingerprints the installed key, so the lease loop can tell a new key from the
  same key handed back again (it survives restarts, which rewrite the profiles' ``.env``);
  ``key_changed_at`` is when a different key was last installed, so the hook can ignore
  billing errors from runs still finishing on the previous, spent key.

``budget_exhausted`` holds until the lease loop installs a different key; its ``until`` (the
refill time) is only what the user is told. It does not lapse by the clock, because the gate
would otherwise open in the seconds between the refill and the new key, runs on the spent key
would fail, and the hook would block again until the following day. A dead lease loop still
closes the gate, through the lease's expiry. ``provider_unavailable`` holds while ``until``
is in the future and simply lapses; nobody clears it. A missing or
malformed state file means "not blocked". Every write is a locked read-modify-write that
replaces the file atomically (temp file, then rename).

Standard library only: the plugin imports this module inside Hermes's own interpreter, and
the image imports it from the release's core source.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from research_core.locks import file_lock

STATE_NAME = ".inference-state"
LOCK_TIMEOUT_S = 5
# The platform's lease outlives the refill by this grace period (metta RENEWAL_GRACE), so a
# lease's expires_at minus the grace is the next refill time.
RENEWAL_GRACE = timedelta(minutes=10)

Blocked = Literal["budget_exhausted", "provider_unavailable"]


@dataclass(frozen=True)
class ApiError:
    """The last failed model call Hermes reported through ``api_request_error``."""

    session_id: str
    reason: str
    status_code: int | None
    at: datetime


@dataclass(frozen=True)
class InferenceState:
    blocked: Blocked | None = None
    until: datetime | None = None
    since: datetime | None = None
    last_error: ApiError | None = None
    next_refill_at: datetime | None = None
    outages: tuple[datetime, ...] = field(default_factory=tuple)
    key_id: str | None = None
    key_changed_at: datetime | None = None

    def blocking(self, now: datetime | None = None) -> bool:
        """Whether this state stops model work at ``now``: a budget block until a new key, an
        outage block until ``until``."""
        if self.blocked == "budget_exhausted":
            return True
        return (
            self.blocked is not None
            and self.until is not None
            and self.until > (now or datetime.now(UTC))
        )

    def with_block(self, blocked: Blocked | None, until: datetime | None, now: datetime):
        """This state with ``blocked``/``until`` set; ``since`` moves only when ``blocked`` does."""
        since = self.since if blocked == self.blocked and self.since else now
        return replace(self, blocked=blocked, until=until, since=since)


def state_path(lease_file: Path) -> Path:
    return lease_file.with_name(STATE_NAME)


def read_state(lease_file: Path | None) -> InferenceState:
    """The inference state beside ``lease_file``; a missing or malformed file reads as empty."""
    if lease_file is None:
        return InferenceState()
    try:
        return _decode(json.loads(state_path(lease_file).read_text()))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return InferenceState()


def update_state(
    lease_file: Path, change: Callable[[InferenceState], InferenceState]
) -> InferenceState:
    """Apply ``change`` to the current state under a lock and replace the file atomically."""
    path = state_path(lease_file)
    with file_lock(path.with_name(STATE_NAME + ".lock"), timeout_s=LOCK_TIMEOUT_S):
        state = change(read_state(lease_file))
        pending = path.with_name(STATE_NAME + ".pending")
        pending.write_text(json.dumps(_encode(state), indent=2) + "\n")
        os.replace(pending, path)
    return state


def blocking_state(lease_file: Path | None, now: datetime | None = None) -> InferenceState | None:
    """The inference state if it blocks model work now, else ``None``."""
    state = read_state(lease_file)
    return state if state.blocking(now) else None


def available(lease_file: Path | None, now: datetime | None = None) -> bool:
    """Whether model work may start: the lease is unexpired and the state does not block.

    ``lease_file`` is ``None`` outside a platform-managed workspace: always available.
    """
    if lease_file is None:
        return True
    now = now or datetime.now(UTC)
    try:
        expires_at = datetime.fromisoformat(lease_file.read_text())
    except (OSError, ValueError):
        return False
    return expires_at > now and not read_state(lease_file).blocking(now)


def next_refill(state: InferenceState, now: datetime) -> datetime:
    """When the budget refills next: the recorded time, else the next 00:00 UTC."""
    if state.next_refill_at is not None and state.next_refill_at > now:
        return state.next_refill_at
    return datetime.combine(
        now.astimezone(UTC).date() + timedelta(days=1), datetime.min.time(), UTC
    )


def key_id(key: str) -> str:
    """A short fingerprint of a model key, so the state can tell keys apart without holding one."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def lease_installed(
    lease_file: Path, expires_at: datetime, key: str, now: datetime | None = None
) -> InferenceState:
    """The lease loop installed a lease: record the refill time and the key's fingerprint.

    A different key ends a budget block. The platform hands back the same key until it nears
    expiry, and a spent key stays spent until the refill, so the same key changes nothing.
    """
    now = now or datetime.now(UTC)

    def change(state: InferenceState) -> InferenceState:
        installed = key_id(key)
        if state.key_id != installed:
            state = replace(state, key_id=installed, key_changed_at=now)
            if state.blocked == "budget_exhausted":
                state = state.with_block(None, None, now)
        return replace(state, next_refill_at=expires_at - RENEWAL_GRACE)

    return update_state(lease_file, change)


def lease_refused(
    lease_file: Path,
    next_refill_at: datetime,
    *,
    budget_exhausted: bool,
    now: datetime | None = None,
) -> InferenceState:
    """The platform refused a lease: record the refill time; a spent budget blocks until it."""
    now = now or datetime.now(UTC)

    def change(state: InferenceState) -> InferenceState:
        state = replace(state, next_refill_at=next_refill_at)
        if budget_exhausted:
            state = state.with_block("budget_exhausted", next_refill_at, now)
        return state

    return update_state(lease_file, change)


# -- JSON ---------------------------------------------------------------------------------------


def _time(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:  # a naive time cannot be compared with now: malformed
        raise ValueError(value)
    return parsed


def _decode(raw: dict[str, Any]) -> InferenceState:
    blocked = raw.get("blocked")
    if blocked not in (None, "budget_exhausted", "provider_unavailable"):
        blocked = None
    error = raw.get("last_error")
    return InferenceState(
        blocked=blocked,
        until=_time(raw.get("until")),
        since=_time(raw.get("since")),
        last_error=None
        if error is None
        else ApiError(
            session_id=str(error["session_id"]),
            reason=str(error["reason"]),
            status_code=error.get("status_code"),
            at=_time(error["at"]),
        ),
        next_refill_at=_time(raw.get("next_refill_at")),
        outages=tuple(_time(at) for at in raw.get("outages") or ()),
        key_id=raw.get("key_id"),
        key_changed_at=_time(raw.get("key_changed_at")),
    )


def _encode(state: InferenceState) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()

    return {
        "blocked": state.blocked,
        "until": iso(state.until),
        "since": iso(state.since),
        "last_error": None
        if state.last_error is None
        else asdict(state.last_error) | {"at": state.last_error.at.isoformat()},
        "next_refill_at": iso(state.next_refill_at),
        "outages": [at.isoformat() for at in state.outages],
        "key_id": state.key_id,
        "key_changed_at": iso(state.key_changed_at),
    }
