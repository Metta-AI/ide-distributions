"""Durable client receipts and queued chat inputs, owned by the workspace."""

import fcntl
import json
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, NonNegativeFloat

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
    input: dict
    status: Literal["queued", "running", "completed", "cancelled", "failed", "interrupted"]
    run_id: str | None
    error: str | None
    created_at: str
    client_id: str | None


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str | None
    replayed: bool
    writer_wait_ms: NonNegativeFloat
    write_ms: NonNegativeFloat
    commit_ms: NonNegativeFloat


class IntakeTiming(Receipt):
    event: Literal["workspace_intake"] = "workspace_intake"
    operation: Literal["session.open", "session.message"]
    op_id: str
    validation_ms: NonNegativeFloat
    store_open_ms: NonNegativeFloat
    metadata_ms: NonNegativeFloat
    registration_ms: NonNegativeFloat = 0
    total_ms: NonNegativeFloat

    def emit(self) -> None:
        sys.stderr.write(self.model_dump_json() + "\n")
        sys.stderr.flush()


def read(store: Store, op_id: str, kind: str, request: dict) -> dict | None:
    row = store.one("SELECT * FROM client_admissions WHERE op_id = ?", (op_id,))
    if row is None:
        return None
    if row["kind"] != kind or json.loads(row["request"]) != request:
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
) -> Receipt:
    started = time.monotonic()
    with store.tx(actor) as tx:
        acquired = time.monotonic()
        existing = read(store, op_id, kind, request)
        if existing is None:
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
                    "queued",
                    tx.at,
                ),
            )
            tx.event("client.accepted", op_id, {"kind": kind, "session_id": session_id})
        written = time.monotonic()
    committed = time.monotonic()
    return Receipt(
        session_id=existing["session_id"] if existing is not None else session_id,
        run_id=existing["run_id"] if existing is not None else None,
        replayed=existing is not None,
        writer_wait_ms=(acquired - started) * 1000,
        write_ms=(written - acquired) * 1000,
        commit_ms=(committed - written) * 1000,
    )


def snapshot(store: Store) -> list[dict]:
    return [
        asdict(Admission(**{**dict(row), "input": json.loads(row["input"])}))
        for row in store.q(
            "SELECT op_id,session_id,message,json_extract(request, '$.message') AS input,"
            " status,run_id,error,created_at,"
            " json_extract(payload, '$.context.client.id') AS client_id"
            " FROM client_admissions WHERE kind = 'message' ORDER BY rowid"
        )
    ]
