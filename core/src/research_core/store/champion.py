"""The champion record: the policy version that represents the Player in a league (B.6).

Never the league's winner. One row per league, kept in step with the platform by the
watcher's champion sync (every ten minutes) and by the submit tool. The platform's path is
submission -> ``pending`` -> ``processing`` -> ``placed`` or ``rejected`` -> ``champion``;
``none`` is an empty league. ``sync`` writes ``champion_history`` and emits ``champion.changed``
only when the policy version or its state changes, so an unchanged sync leaves no event behind
and only refreshes ``synced`` (and the division and membership ids). ``since`` is when the
current policy version and state began. ``approved_by`` is the submit tool's to set; sync
never touches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from research_core.store import versions
from research_core.store._common import parse_display_id, require
from research_core.store.errors import StoreError
from research_core.store.registry import CHAMPION, CHAMPION_STATES, record_from_row

if TYPE_CHECKING:
    from research_core.store.actors import Actor
    from research_core.store.db import Store

STATES = CHAMPION_STATES


def sync(
    store: Store,
    actor: Actor,
    *,
    league_id: str,
    division_id: str | None,
    membership_id: str | None,
    policy_version_id: str | None,
    state: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Upsert the league's row from what the platform reports.

    ``version_id`` is filled from the local Version uploaded as that policy version, when there
    is one. A ``reason`` given here is kept until a later sync gives another. History and the
    ``champion.changed`` event are written only when ``policy_version_id`` or ``state`` differ
    from the stored row.
    """
    problems: list[str] = []
    require(problems, _is_text(league_id), "league_id is required")
    require(problems, division_id is None or _is_text(division_id), "division_id must be text")
    require(
        problems, membership_id is None or _is_text(membership_id), "membership_id must be text"
    )
    require(problems, state in STATES, f"state must be {_states_text()}")
    if policy_version_id is not None:
        require(problems, _is_text(policy_version_id), "policy_version_id must be text")
    elif state != "none":
        problems.append("policy_version_id is required unless state is none")
    require(problems, reason is None or _is_text(reason), "reason must be text")
    if problems:
        raise StoreError(problems)
    version_id = _local_version_id(store, policy_version_id)
    with store.tx(actor) as tx:
        old = tx.one("SELECT * FROM champion WHERE league_id = ?", (league_id,))
        changed = (
            old is None or old["policy_version_id"] != policy_version_id or old["state"] != state
        )
        since = tx.at if changed else old["since"]
        if old is None:
            tx.execute(
                "INSERT INTO champion(league_id, division_id, membership_id, version_id,"
                " policy_version_id, state, since, reason, synced)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    league_id,
                    division_id,
                    membership_id,
                    version_id,
                    policy_version_id,
                    state,
                    since,
                    reason,
                    tx.at,
                ),
            )
        else:
            tx.execute(
                "UPDATE champion SET division_id = ?, membership_id = ?, version_id = ?,"
                " policy_version_id = ?, state = ?, since = ?, reason = COALESCE(?, reason),"
                " synced = ? WHERE league_id = ?",
                (
                    division_id,
                    membership_id,
                    version_id,
                    policy_version_id,
                    state,
                    since,
                    reason,
                    tx.at,
                    league_id,
                ),
            )
        if changed:
            tx.execute(
                "INSERT INTO champion_history(league_id, policy_version_id, state, at, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (league_id, policy_version_id, state, tx.at, reason),
            )
            previous = None
            if old is not None:
                previous = {"policy_version_id": old["policy_version_id"], "state": old["state"]}
            tx.event(
                "champion.changed",
                league_id,
                {
                    "league_id": league_id,
                    "policy_version_id": policy_version_id,
                    "state": state,
                    "version_id": version_id,
                    "previous": previous,
                },
            )
    return read(store, league_id)


def read(store: Store, league_id: str) -> dict[str, Any] | None:
    """The league's champion record with its history, newest first; ``None`` when unknown."""
    row = store.one("SELECT * FROM champion WHERE league_id = ?", (league_id,))
    if row is None:
        return None
    record = record_from_row(CHAMPION, row)
    history = store.q(
        "SELECT policy_version_id, state, at, note FROM champion_history"
        " WHERE league_id = ? ORDER BY id DESC",
        (league_id,),
    )
    record["history"] = [dict(entry) for entry in history]
    return record


def _local_version_id(store: Store, policy_version_id: str | None) -> int | None:
    if policy_version_id is None:
        return None
    local = versions.find_by_policy_version(store, policy_version_id)
    if local is None:
        return None
    return parse_display_id(local["id"])[1]


def _states_text() -> str:
    return ", ".join(STATES[:-1]) + f" or {STATES[-1]}"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
