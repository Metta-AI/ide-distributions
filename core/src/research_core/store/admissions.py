"""Durable client receipts and queued chat inputs, owned by the workspace."""

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Literal

from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError


@contextmanager
def dispatch_lock(store: Store):
    """Serialize the start/receipt boundary with the separate sleep command."""
    with (store.path.parent / "client-dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Admission:
    op_id: str
    session_id: str
    message: str
    status: Literal["queued", "running", "completed", "cancelled", "failed", "interrupted"]
    run_id: str | None
    error: str | None
    created_at: str


def read(store: Store, op_id: str, kind: str, request: dict) -> dict | None:
    row = store.one("SELECT * FROM client_admissions WHERE op_id = ?", (op_id,))
    if row is None:
        return None
    if row["kind"] != kind or row["request"] != json.dumps(request, sort_keys=True):
        raise StoreError(["op_id was already used for different input"])
    return dict(row)


def accept(
    store: Store,
    actor: Actor,
    *,
    op_id: str,
    kind: str,
    request: dict,
    session_id: str,
    message: str,
    payload: str,
) -> None:
    with store.tx(actor) as tx:
        tx.execute(
            "INSERT INTO client_admissions"
            " (op_id,kind,request,session_id,message,payload,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                op_id,
                kind,
                json.dumps(request, sort_keys=True),
                session_id,
                message,
                payload,
                "queued" if kind == "message" else "completed",
                tx.at,
            ),
        )
        tx.event("client.accepted", op_id, {"kind": kind, "session_id": session_id})


def snapshot(store: Store) -> list[dict]:
    return [
        asdict(Admission(**dict(row)))
        for row in store.q(
            "SELECT op_id,session_id,message,status,run_id,error,created_at"
            " FROM client_admissions WHERE kind = 'message' ORDER BY rowid"
        )
    ]
