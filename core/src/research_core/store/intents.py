"""Intents (design B.13): one row per external action, written before the platform call.

Before an upload, a submission, an XP request, a forum or wiki write, a run start or a champion
change, the tool calls ``begin`` with a key that identifies the action (the artifact hash, the
Version and league, the request number, the text hash, the session and attempt). The row stays
``pending`` until ``finish`` records what the platform answered. A crash leaves it ``pending``;
before any retry the caller reconciles the key against the platform and finishes the row with
what it finds. ``begin`` on an existing row never sends anything twice blindly:

- a ``pending`` or ``done`` row comes back as it is with ``existing`` true, and the caller
  decides (reconcile, or treat the action as already done);
- a ``failed`` row is a new attempt: it goes back to ``pending``, the earlier failed results are
  kept under ``result["failures"]`` (a reserved key) and ``attempt`` counts them plus one.

Events: ``intent.pending`` for every attempt, then ``intent.done`` or ``intent.failed``. The
event subject is the intent's display id (``I-<n>``); the record the action is for is
``subject`` in the payload. Rows live in the Coworld store.
"""

from __future__ import annotations

from typing import Any

from research_core.store._common import display_id, json_field, parse_json, require
from research_core.store.actors import Actor
from research_core.store.db import Store, Tx
from research_core.store.errors import NotFound, StoreError
from research_core.store.registry import INTENT_KINDS, RECORDS, record_from_row

SPEC = RECORDS["intent"]
FINAL_STATES = ("done", "failed")
# Earlier failed results of a retried intent live under this key of ``result``.
FAILURES_KEY = "failures"


def begin(
    store: Store, actor: Actor, *, kind: str, key: str, subject: str | None = None
) -> dict[str, Any]:
    """Record the intent to act; returns the row with ``existing`` and ``retried`` flags."""
    problems: list[str] = []
    require(problems, kind in INTENT_KINDS, f"kind is one of {', '.join(INTENT_KINDS)}")
    require(problems, isinstance(key, str) and key, "key is a non-empty string")
    require(
        problems,
        subject is None or (isinstance(subject, str) and subject),
        "subject is a record reference such as V-7, or None",
    )
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        row = tx.one("SELECT * FROM intents WHERE kind = ? AND key = ?", (kind, key))
        if row is None:
            cursor = tx.execute(
                "INSERT INTO intents(kind, subject, key, state, created, created_by)"
                " VALUES (?, ?, ?, 'pending', ?, ?)",
                (kind, subject, key, tx.at, str(actor)),
            )
            record = _record(tx, cursor.lastrowid)
            tx.event("intent.pending", record["id"], _payload(record) | {"attempt": 1})
            return {**record, "existing": False, "retried": False}
        if row["state"] != "failed":
            return {**_record_from(row), "existing": True, "retried": False}
        failures = _failures(row) + [_answer(row)]
        tx.execute(
            "UPDATE intents SET state = 'pending', result = ?, resolved = NULL WHERE id = ?",
            (json_field({FAILURES_KEY: failures}), row["id"]),
        )
        record = _record(tx, row["id"])
        tx.event("intent.pending", record["id"], _payload(record) | {"attempt": record["attempt"]})
        return {**record, "existing": True, "retried": True}


def finish(
    store: Store, actor: Actor, intent_id: int, *, state: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a pending intent with what the platform answered; emits ``intent.<state>``."""
    problems: list[str] = []
    require(problems, _is_int(intent_id), "intent_id is an integer")
    require(problems, state in FINAL_STATES, "state is done or failed")
    require(problems, isinstance(result, dict), "result is a JSON object (the platform's answer)")
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        row = tx.one("SELECT * FROM intents WHERE id = ?", (intent_id,))
        if row is None:
            raise NotFound([f"{display_id('I', intent_id)} does not exist"])
        if row["state"] != "pending":
            raise StoreError([f"{display_id('I', intent_id)} is already {row['state']}"])
        stored = dict(result)
        failures = _failures(row)
        if failures:
            stored[FAILURES_KEY] = failures
        tx.execute(
            "UPDATE intents SET state = ?, result = ?, resolved = ? WHERE id = ?",
            (state, json_field(stored), tx.at, intent_id),
        )
        record = _record(tx, intent_id)
        tx.event(f"intent.{state}", record["id"], _payload(record) | {"result": record["result"]})
        return record


def pending(store: Store, kind: str | None = None) -> list[dict[str, Any]]:
    """Pending intents in creation order, optionally of one kind."""
    if kind is None:
        rows = store.q("SELECT * FROM intents WHERE state = 'pending' ORDER BY id")
    else:
        if kind not in INTENT_KINDS:
            raise StoreError([f"kind is one of {', '.join(INTENT_KINDS)}"])
        rows = store.q(
            "SELECT * FROM intents WHERE state = 'pending' AND kind = ? ORDER BY id", (kind,)
        )
    return [_record_from(row) for row in rows]


def read(store: Store, intent_id: int) -> dict[str, Any]:
    row = store.one("SELECT * FROM intents WHERE id = ?", (intent_id,))
    if row is None:
        raise NotFound([f"{display_id('I', intent_id)} does not exist"])
    return _record_from(row)


# --- helpers -----------------------------------------------------------------------------


def _record(tx: Tx, intent_id: int) -> dict[str, Any]:
    return _record_from(tx.one("SELECT * FROM intents WHERE id = ?", (intent_id,)))


def _record_from(row: Any) -> dict[str, Any]:
    record = record_from_row(SPEC, row)
    record["attempt"] = 1 + len(_failures(row))
    return record


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    return {"kind": record["kind"], "key": record["key"], "subject": record["subject"]}


def _failures(row: Any) -> list[dict[str, Any]]:
    result = parse_json(row["result"], default={})
    return list(result.get(FAILURES_KEY, ()))


def _answer(row: Any) -> dict[str, Any]:
    """The platform's answer stored on a row, without the bookkeeping key."""
    result = parse_json(row["result"], default={})
    return {k: v for k, v in result.items() if k != FAILURES_KEY}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
