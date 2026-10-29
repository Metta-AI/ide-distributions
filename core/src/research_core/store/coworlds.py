"""The Coworld registry in the workspace store (design B.14, amendment A2.5).

One row per Coworld the workspace knows, keyed by the platform Coworld id (the only stored
identifier); ``folder`` is its workspace folder. At most one row is active: the partial unique
index ``one_active`` enforces it and ``activate`` flips it in one transaction. A Coworld counts
as set up once ``mark_set_up`` recorded passing tool checks (``set_up_at``).

``pending_request`` (amendments A4.8 and A6.3) is the user's request that setup carries out
when it finishes, ``{text, cites}``. Migration 0001 gives the row no ``pending_request`` column,
so until a migration adds one the request is kept in the workspace store's ``meta`` table under
``pending_request:<coworld_id>`` and merged into every record this module returns; callers see
one shape either way and nothing else reads that key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from research_core.store._common import json_field, parse_json, require
from research_core.store.errors import NotFound, StoreError
from research_core.store.registry import COWORLD, record_from_row

if TYPE_CHECKING:
    import sqlite3

    from research_core.store.actors import Actor
    from research_core.store.db import Store

PENDING_REQUEST_META_KEY = "pending_request:{coworld_id}"


def _is_text(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _fetch(wstore: Store, coworld_id: str) -> sqlite3.Row:
    row = wstore.one("SELECT * FROM coworlds WHERE coworld_id = ?", (coworld_id,))
    if row is None:
        raise NotFound([f"Coworld {coworld_id} is not registered"])
    return row


def _record(wstore: Store, row: sqlite3.Row) -> dict[str, Any]:
    record = record_from_row(COWORLD, row)
    record["active"] = bool(record["active"])
    key = PENDING_REQUEST_META_KEY.format(coworld_id=row["coworld_id"])
    record["pending_request"] = parse_json(wstore.get_meta(key), default=None)
    return record


def register(wstore: Store, actor: Actor, coworld_id: str, *, folder: str) -> dict[str, Any]:
    """Add a Coworld to the registry (inactive); event ``coworld.registered``.

    Registering an id again changes nothing and returns the row with ``existing: True``.
    """
    problems: list[str] = []
    require(problems, _is_text(coworld_id), "coworld_id is required: the platform Coworld id")
    require(problems, _is_text(folder), "folder is required: the workspace folder name")
    if problems:
        raise StoreError(problems)
    with wstore.tx(actor) as tx:
        existing = tx.one("SELECT * FROM coworlds WHERE coworld_id = ?", (coworld_id,))
        if existing is not None:
            return {**_record(wstore, existing), "existing": True}
        holder = tx.one("SELECT coworld_id FROM coworlds WHERE folder = ?", (folder,))
        if holder is not None:
            raise StoreError([f"folder {folder} is registered to {holder[0]}"])
        tx.execute(
            "INSERT INTO coworlds(coworld_id, folder, active, created) VALUES (?, ?, 0, ?)",
            (coworld_id, folder, tx.at),
        )
        tx.event("coworld.registered", coworld_id, {"folder": folder})
    return {**read(wstore, coworld_id), "existing": False}


def activate(wstore: Store, actor: Actor, coworld_id: str) -> dict[str, Any]:
    """Make ``coworld_id`` the one active Coworld; event ``coworld.switched`` when it changes."""
    with wstore.tx(actor) as tx:
        _fetch(wstore, coworld_id)
        current = tx.one("SELECT coworld_id FROM coworlds WHERE active = 1")
        previous = None if current is None else current[0]
        if previous != coworld_id:
            tx.execute("UPDATE coworlds SET active = 0 WHERE active = 1")
            tx.execute("UPDATE coworlds SET active = 1 WHERE coworld_id = ?", (coworld_id,))
            tx.event("coworld.switched", coworld_id, {"from": previous, "to": coworld_id})
    return read(wstore, coworld_id)


def active(wstore: Store) -> dict[str, Any] | None:
    row = wstore.one("SELECT * FROM coworlds WHERE active = 1")
    return None if row is None else _record(wstore, row)


def read(wstore: Store, coworld_id: str) -> dict[str, Any]:
    return _record(wstore, _fetch(wstore, coworld_id))


def list(wstore: Store) -> list[dict[str, Any]]:
    """Every registered Coworld, in registration order."""
    rows = wstore.q("SELECT * FROM coworlds ORDER BY created, coworld_id")
    return [_record(wstore, row) for row in rows]


def mark_set_up(
    wstore: Store, actor: Actor, coworld_id: str, *, mixin_version: str, tool_check_status: str
) -> dict[str, Any]:
    """Record that setup finished with its mixin and tool checks; event ``coworld.set_up``.

    Called only after the checks passed (B.14): ``set_up_at`` means the Coworld is usable.
    """
    problems: list[str] = []
    require(problems, _is_text(mixin_version), "mixin_version is required")
    require(problems, _is_text(tool_check_status), "tool_check_status is required")
    with wstore.tx(actor) as tx:
        _fetch(wstore, coworld_id)
        if problems:
            raise StoreError(problems)
        tx.execute(
            "UPDATE coworlds SET mixin_version = ?, tool_check_status = ?, set_up_at = ?"
            " WHERE coworld_id = ?",
            (mixin_version, tool_check_status, tx.at, coworld_id),
        )
        tx.event(
            "coworld.set_up",
            coworld_id,
            {"mixin_version": mixin_version, "tool_check_status": tool_check_status},
        )
    return read(wstore, coworld_id)


def set_pending_request(
    wstore: Store, actor: Actor, coworld_id: str, request: dict[str, Any] | None
) -> dict[str, Any]:
    """Store (or with ``None`` clear) the request setup carries out when it finishes.

    No event: the switch tool copies the request into ``coworld.setup_needed`` (A6.3). See the
    module docstring for where the value lives until the row has its column.
    """
    if request is not None and not isinstance(request, dict):
        raise StoreError(["pending_request must be an object such as {text, cites}, or null"])
    key = PENDING_REQUEST_META_KEY.format(coworld_id=coworld_id)
    with wstore.tx(actor) as tx:
        _fetch(wstore, coworld_id)
        if request is None:
            tx.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            tx.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json_field(request))
            )
    return read(wstore, coworld_id)
