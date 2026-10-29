"""Pins: downloaded artifacts and replays kept by ``(kind, ref)`` in the Coworld store.

A pin records where a download landed (``download``: path, digest, size and whatever else the
fetching tool knows), so one replay or artifact is fetched once and cited by a stable id
(measurement evidence names "a stored output path or pin id", design B.7). ``add`` returns the
existing pin for a ``(kind, ref)`` already pinned, with ``existing`` true, instead of a second
download record. Pins are bookkeeping and emit no event.
"""

from __future__ import annotations

from typing import Any

from research_core.store._common import json_field, parse_json, require
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError


def add(
    store: Store, actor: Actor, *, kind: str, ref: str, download: dict[str, Any]
) -> dict[str, Any]:
    """Pin a download; an existing ``(kind, ref)`` comes back unchanged with ``existing`` true."""
    problems: list[str] = []
    require(problems, _is_text(kind), "kind is a non-empty string (replay, artifact, ...)")
    require(problems, _is_text(ref), "ref is a non-empty string")
    require(problems, isinstance(download, dict), "download is a JSON object (path, digest, ...)")
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        row = tx.one("SELECT * FROM pins WHERE kind = ? AND ref = ?", (kind, ref))
        if row is not None:
            return {**_record(row), "existing": True}
        cursor = tx.execute(
            "INSERT INTO pins(kind, ref, download, created) VALUES (?, ?, ?, ?)",
            (kind, ref, json_field(download), tx.at),
        )
        row = tx.one("SELECT * FROM pins WHERE id = ?", (cursor.lastrowid,))
        return {**_record(row), "existing": False}


def find(store: Store, kind: str, ref: str) -> dict[str, Any] | None:
    row = store.one("SELECT * FROM pins WHERE kind = ? AND ref = ?", (kind, ref))
    return None if row is None else _record(row)


def _record(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "ref": row["ref"],
        "download": parse_json(row["download"], default={}),
        "created": row["created"],
    }


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)
