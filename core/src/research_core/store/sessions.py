"""Sessions (design B.5): one row per Hermes Session, in the workspace store only (A2.4).

A Session is a chat, a Task run or a wake. Rows are registered before any Coworld is active,
so they live in the workspace store: ``coworld_id`` names the Coworld store that ``task_id``
points into, and ``task_id`` carries no foreign key. ``register`` upserts (fields given again
replace, fields left ``None`` are kept, and a Session registered again after it ended is live
again) and emits ``session.started`` on insert only; ``end`` emits ``session.ended`` once. A
Session's status (running, waiting, stopped) is computed from the Hermes runs API by the
watcher and never stored. ``set_run`` records the run the watcher started and counts it in
``attempts`` unless the caller sets the count.

``stopped_by`` (A5.6: ``watcher:drain``, ``watcher:replace``, ``watcher:abandon``) marks a
stop the watcher asked for, so recovery does not treat the run's end as a death. The
workspace migration has no ``stopped_by`` column yet, so the mark is kept in the store's
``meta`` table under ``stopped_by:<session id>`` and read back into every record; a later
migration can move it into the table without changing this module's interface. ``set_run``
clears it: a new run is live and a later end is a real one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from research_core.store._common import json_field, require
from research_core.store.actors import Actor
from research_core.store.db import Store, Tx
from research_core.store.errors import NotFound, StoreError
from research_core.store.registry import RECORDS, record_from_row

SPEC = RECORDS["session"]
KINDS = ("chat", "task", "wake")


def register(
    store: Store,
    actor: Actor,
    *,
    session_id: str,
    profile: str,
    kind: str,
    coworld_id: str | None = None,
    task_id: int | None = None,
    goal_id: int | None = None,
    event: str | None = None,
    parent: str | None = None,
    session_context: dict[str, Any] | None = None,
    client_capabilities: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    """Upsert a Session; ``session.started`` on insert. The record carries ``created``."""
    _require_workspace(store)
    problems: list[str] = []
    require(problems, _is_text(session_id), "session_id is a non-empty string")
    require(problems, _is_text(profile), "profile is a non-empty string")
    require(problems, kind in KINDS, f"kind is one of {', '.join(KINDS)}")
    if kind == "task":
        require(problems, task_id is not None, "a task Session names its task_id")
    require(problems, task_id is None or _is_int(task_id), "task_id is an integer or None")
    require(problems, goal_id is None or _is_int(goal_id), "goal_id is an integer or None")
    for name, value in (("coworld_id", coworld_id), ("event", event), ("parent", parent)):
        require(problems, value is None or _is_text(value), f"{name} is a non-empty string or None")
    require(
        problems,
        session_context is None or isinstance(session_context, dict),
        "session_context is a JSON object or None",
    )
    require(
        problems,
        client_capabilities is None or isinstance(client_capabilities, dict | list),
        "client_capabilities is a JSON object or list, or None",
    )
    if problems:
        raise StoreError(problems)
    context = None if session_context is None else json_field(session_context)
    capabilities = None if client_capabilities is None else json_field(client_capabilities)
    with store.tx(actor) as tx:
        created = tx.one("SELECT 1 FROM sessions WHERE id = ?", (session_id,)) is None
        if created:
            tx.execute(
                "INSERT INTO sessions(id, profile, kind, coworld_id, task_id, goal_id, event,"
                " parent, started, last_seen, session_context, client_capabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    profile,
                    kind,
                    coworld_id,
                    task_id,
                    goal_id,
                    event,
                    parent,
                    tx.at,
                    tx.at,
                    context,
                    capabilities,
                ),
            )
            tx.event(
                "session.started",
                session_id,
                {
                    "profile": profile,
                    "kind": kind,
                    "coworld_id": coworld_id,
                    "task_id": task_id,
                    "goal_id": goal_id,
                    "event": event,
                    "parent": parent,
                },
            )
        else:
            tx.execute(
                "UPDATE sessions SET profile = ?, kind = ?, coworld_id = COALESCE(?, coworld_id),"
                " task_id = COALESCE(?, task_id), goal_id = COALESCE(?, goal_id),"
                " event = COALESCE(?, event), parent = COALESCE(?, parent),"
                " session_context = COALESCE(?, session_context),"
                " client_capabilities = COALESCE(?, client_capabilities),"
                " ended = NULL, last_seen = ? WHERE id = ?",
                (
                    profile,
                    kind,
                    coworld_id,
                    task_id,
                    goal_id,
                    event,
                    parent,
                    context,
                    capabilities,
                    tx.at,
                    session_id,
                ),
            )
        return {**_record(tx, session_id), "created": created}


def set_run(
    store: Store, actor: Actor, session_id: str, run_id: str, attempts: int | None = None
) -> dict[str, Any]:
    """A run started for the Session: record it, count the attempt, make the Session live."""
    _require_workspace(store)
    problems: list[str] = []
    require(problems, _is_text(run_id), "run_id is a non-empty string")
    require(
        problems,
        attempts is None or (_is_int(attempts) and attempts >= 0),
        "attempts is a non-negative integer or None",
    )
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        _require_session(tx, session_id)
        if attempts is None:
            tx.execute(
                "UPDATE sessions SET run_id = ?, attempts = attempts + 1, ended = NULL,"
                " last_seen = ? WHERE id = ?",
                (run_id, tx.at, session_id),
            )
        else:
            tx.execute(
                "UPDATE sessions SET run_id = ?, attempts = ?, ended = NULL, last_seen = ?"
                " WHERE id = ?",
                (run_id, attempts, tx.at, session_id),
            )
        tx.execute("DELETE FROM meta WHERE key = ?", (_stopped_key(session_id),))
        return _record(tx, session_id)


def end(store: Store, actor: Actor, session_id: str) -> dict[str, Any]:
    """The Session's run ended; ``session.ended`` once (ending again changes nothing)."""
    _require_workspace(store)
    with store.tx(actor) as tx:
        row = _require_session(tx, session_id)
        if row["ended"] is not None:
            return _record(tx, session_id)
        tx.execute("UPDATE sessions SET ended = ? WHERE id = ?", (tx.at, session_id))
        record = _record(tx, session_id)
        tx.event(
            "session.ended",
            session_id,
            {
                "profile": record["profile"],
                "kind": record["kind"],
                "task_id": record["task_id"],
                "run_id": record["run_id"],
                "stopped_by": record["stopped_by"],
            },
        )
        return record


def read(store: Store, session_id: str) -> dict[str, Any] | None:
    _require_workspace(store)
    if store.one("SELECT 1 FROM sessions WHERE id = ?", (session_id,)) is None:
        return None
    return _record(store, session_id)


def for_task(store: Store, task_id: int, *, coworld_id: str | None = None) -> list[dict[str, Any]]:
    """Sessions that worked on a Task, oldest first; ``coworld_id`` narrows to one Coworld."""
    _require_workspace(store)
    if coworld_id is None:
        rows = store.q("SELECT id FROM sessions WHERE task_id = ? ORDER BY started, id", (task_id,))
    else:
        rows = store.q(
            "SELECT id FROM sessions WHERE task_id = ? AND coworld_id = ? ORDER BY started, id",
            (task_id, coworld_id),
        )
    return [_record(store, row["id"]) for row in rows]


def mark_drained(
    store: Store, actor: Actor, session_ids: Sequence[str], drained: bool
) -> list[dict[str, Any]]:
    """Flag Sessions a pause or switch stopped (``True``) or resumed (``False``)."""
    _require_workspace(store)
    problems: list[str] = []
    require(
        problems,
        isinstance(session_ids, list | tuple) and all(_is_text(s) for s in session_ids),
        "session_ids is a list of session ids",
    )
    require(problems, isinstance(drained, bool), "drained is true or false")
    if problems:
        raise StoreError(problems)
    if not session_ids:
        return []
    with store.tx(actor) as tx:
        missing = [
            s for s in session_ids if tx.one("SELECT 1 FROM sessions WHERE id = ?", (s,)) is None
        ]
        if missing:
            raise NotFound([f"session {s} does not exist" for s in missing])
        for session_id in session_ids:
            tx.execute("UPDATE sessions SET drained = ? WHERE id = ?", (int(drained), session_id))
        return [_record(tx, s) for s in session_ids]


def set_stopped_by(
    store: Store, actor: Actor, session_id: str, reason: str | None
) -> dict[str, Any]:
    """Mark a stop the watcher asked for (``watcher:drain|replace|abandon``); ``None`` clears."""
    _require_workspace(store)
    if not (reason is None or _is_text(reason)):
        raise StoreError(["reason is a string such as watcher:drain, or None to clear it"])
    with store.tx(actor) as tx:
        _require_session(tx, session_id)
        key = _stopped_key(session_id)
        if reason is None:
            tx.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            tx.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, reason))
        return _record(tx, session_id)


# --- helpers -----------------------------------------------------------------------------


def _require_workspace(store: Store) -> None:
    if store.kind != "workspace":
        raise StoreError(["sessions live in the workspace store"])


def _require_session(tx: Tx, session_id: str) -> Any:
    row = tx.one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if row is None:
        raise NotFound([f"session {session_id} does not exist"])
    return row


def _stopped_key(session_id: str) -> str:
    return f"stopped_by:{session_id}"


def _record(reader: Store | Tx, session_id: str) -> dict[str, Any]:
    row = reader.one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    record = record_from_row(SPEC, row)
    record["drained"] = bool(record["drained"])
    stopped = reader.one("SELECT value FROM meta WHERE key = ?", (_stopped_key(session_id),))
    record["stopped_by"] = None if stopped is None else stopped["value"]
    return record


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
