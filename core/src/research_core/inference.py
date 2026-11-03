"""Whether model work may start: the platform's inference lease and the inference state.

Two files sit side by side in the folder of ``IDE_INFERENCE_LEASE_FILE``:

* the *lease file* is the one record of the installed key::

      {"expires_at": "<ISO time>",
       "key_id": "<sha256 prefix of the installed key>",
       "key_changed_at": "<ISO time>"}

  ``key_id`` fingerprints the key (the file never holds the key itself); ``key_changed_at`` is
  when a key with a different fingerprint was installed, so the hook can ignore billing errors
  from runs still finishing on the previous, spent key. The image's lease loop
  (``image/inference.py``) is its only writer, through :func:`lease_installed`. The image
  deletes the file at startup, so after a restart ``key_changed_at`` is the first install
  time; a budget block still holds, because the state file survives and names the key. A
  bare ISO time (the file's earlier form, still written by test fixtures) reads as an expiry
  with no key known.
* the *inference state* (``.inference-state``) is a small JSON object that says why model work
  is blocked and until when::

      {"blocked": "budget_exhausted" | "provider_unavailable" | null,
       "until": "<ISO time>" | null,
       "since": "<ISO time>",
       "last_error": {"session_id", "reason", "status_code", "at"} | null,
       "next_refill_at": "<ISO time>" | null,
       "outages": ["<ISO time>", ...],
       "blocked_key_id": "<sha256 prefix of the spent key>" | null}

  Three processes share it, which is why it is a file and not a store table: the Hermes
  plugin's ``api_request_error`` hook (``research_core.plugin.inference_hook``) records each
  failed model call and sets the block; the image's lease loop records the next refill time
  when the platform refuses a lease, and sets ``budget_exhausted`` when it refuses for that
  reason; and :func:`available` reads it before every run start. ``next_refill_at`` is the
  refill time the platform last gave with a refusal (the hook's ``until`` when the lease file
  cannot tell); ``outages`` holds the times of recent outage-class errors, so the hook can
  count them across processes; ``blocked_key_id`` fingerprints the key that ran out.

``budget_exhausted`` holds while the lease file's ``key_id`` equals ``blocked_key_id`` (two
unknown keys count as equal, so a block with no key known still holds). Installing a different
key therefore ends the block with no write to the state file: the lease file alone says the
key changed, so no second write can be lost. The platform hands back the same key until it
nears expiry, and a spent key stays spent until the refill, so the same key keeps the block.
Its ``until`` (the refill time) is only what the user is told. It does not lapse by the clock,
because the gate would otherwise open in the seconds between the refill and the new key, runs
on the spent key would fail, and the hook would block again until the following day. A dead
lease loop still closes the gate, through the lease's expiry. ``provider_unavailable`` holds
while ``until`` is in the future and simply lapses; nobody clears it. A missing or malformed
state file means "not blocked". Every state write is a locked read-modify-write that replaces
the file atomically (temp file, then rename).

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
class InstalledLease:
    """The lease file: the installed key's expiry, fingerprint and when it changed."""

    expires_at: datetime
    key_id: str | None = None
    key_changed_at: datetime | None = None


@dataclass(frozen=True)
class InferenceState:
    blocked: Blocked | None = None
    until: datetime | None = None
    since: datetime | None = None
    last_error: ApiError | None = None
    next_refill_at: datetime | None = None
    outages: tuple[datetime, ...] = field(default_factory=tuple)
    blocked_key_id: str | None = None

    def blocking(self, now: datetime | None = None, *, installed_key_id: str | None) -> bool:
        """Whether this state stops model work at ``now``: a budget block while the spent key
        is still the installed one (``installed_key_id``, the lease file's ``key_id``), an
        outage block until ``until``."""
        if self.blocked == "budget_exhausted":
            return self.blocked_key_id == installed_key_id
        return (
            self.blocked is not None
            and self.until is not None
            and self.until > (now or datetime.now(UTC))
        )

    def with_block(
        self,
        blocked: Blocked | None,
        until: datetime | None,
        now: datetime,
        blocked_key_id: str | None = None,
    ):
        """This state with ``blocked``/``until``/``blocked_key_id`` set; ``since`` moves only
        when the block does."""
        same = blocked == self.blocked and blocked_key_id == self.blocked_key_id
        since = self.since if same and self.since else now
        return replace(
            self, blocked=blocked, until=until, since=since, blocked_key_id=blocked_key_id
        )


def read_lease(lease_file: Path | None) -> InstalledLease | None:
    """The lease file, or ``None`` when it is missing or malformed.

    A bare ISO time, the file's earlier form that test fixtures (and branches not yet on this
    format) still write, reads as ``InstalledLease(expires_at)`` with no key known.
    """
    if lease_file is None:
        return None
    try:
        text = lease_file.read_text()
        try:
            raw = json.loads(text)
        except ValueError:
            return InstalledLease(expires_at=_time(text.strip()))
        return InstalledLease(
            expires_at=_time(raw["expires_at"]),
            key_id=raw.get("key_id"),
            key_changed_at=_time(raw.get("key_changed_at")),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _installed_key_id(lease_file: Path | None) -> str | None:
    lease = read_lease(lease_file)
    return None if lease is None else lease.key_id


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
    return state if state.blocking(now, installed_key_id=_installed_key_id(lease_file)) else None


def available(lease_file: Path | None, now: datetime | None = None) -> bool:
    """Whether model work may start: the lease is unexpired and the state does not block.

    ``lease_file`` is ``None`` outside a platform-managed workspace: always available.
    """
    if lease_file is None:
        return True
    now = now or datetime.now(UTC)
    lease = read_lease(lease_file)
    if lease is None:
        return False
    return lease.expires_at > now and not read_state(lease_file).blocking(
        now, installed_key_id=lease.key_id
    )


def next_refill(lease: InstalledLease | None, state: InferenceState, now: datetime) -> datetime:
    """When the budget refills next: the lease's expiry minus the platform's grace, else the
    refill time of the last refusal, else the next 00:00 UTC (whichever first is still ahead)."""
    if lease is not None and lease.expires_at - RENEWAL_GRACE > now:
        return lease.expires_at - RENEWAL_GRACE
    if state.next_refill_at is not None and state.next_refill_at > now:
        return state.next_refill_at
    return datetime.combine(
        now.astimezone(UTC).date() + timedelta(days=1), datetime.min.time(), UTC
    )


def key_id(key: str) -> str:
    """A short fingerprint of a model key, so the files can tell keys apart without holding one."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def lease_installed(
    lease_file: Path, expires_at: datetime, key: str, now: datetime | None = None
) -> InstalledLease:
    """The lease loop installed a lease: write the lease file with the key's fingerprint.

    ``key_changed_at`` moves to ``now`` only when the fingerprint differs from the file's; the
    same key handed back keeps it. The state file is not touched: a different ``key_id`` is
    what ends a budget block (:meth:`InferenceState.blocking`). The lease loop is the file's
    only writer, so the read-then-replace needs no lock; the replace is atomic, so readers see
    the old lease or the new one.
    """
    now = now or datetime.now(UTC)
    installed = key_id(key)
    previous = read_lease(lease_file)
    if previous is not None and previous.key_id == installed:
        changed_at = previous.key_changed_at
    else:
        changed_at = now
    lease = InstalledLease(expires_at=expires_at, key_id=installed, key_changed_at=changed_at)
    pending = lease_file.with_name(lease_file.name + ".pending")
    pending.write_text(
        json.dumps(
            {
                "expires_at": expires_at.isoformat(),
                "key_id": installed,
                "key_changed_at": None if changed_at is None else changed_at.isoformat(),
            }
        )
        + "\n"
    )
    os.replace(pending, lease_file)
    return lease


def lease_refused(
    lease_file: Path,
    next_refill_at: datetime,
    *,
    budget_exhausted: bool,
    now: datetime | None = None,
) -> InferenceState:
    """The platform refused a lease: record the refill time; a spent budget blocks the
    installed key (the lease file's ``key_id``) until a different key arrives."""
    now = now or datetime.now(UTC)
    spent_key_id = _installed_key_id(lease_file)

    def change(state: InferenceState) -> InferenceState:
        state = replace(state, next_refill_at=next_refill_at)
        if budget_exhausted:
            state = state.with_block("budget_exhausted", next_refill_at, now, spent_key_id)
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
        blocked_key_id=raw.get("blocked_key_id"),
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
        "blocked_key_id": state.blocked_key_id,
    }
