"""Durable client receipts and queued chat inputs, owned by the workspace."""

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from research_core.store.actors import Actor
from research_core.store.db import Store, Tx
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


def read(store: Store, op_id: str, kind: str, request: dict) -> dict | None:
    row = store.one("SELECT * FROM client_admissions WHERE op_id = ?", (op_id,))
    if row is None:
        return None
    if row["kind"] != kind or json.loads(row["request"]) != request:
        raise StoreError(["op_id was already used for different input"])
    return dict(row)


def snapshot(store: Store) -> list[dict]:
    return [
        asdict(Admission(**{**dict(row), "input": json.loads(row["input"])}))
        for row in store.q(
            "SELECT op_id,session_id,message,"
            " CASE WHEN origin='answer' THEN json_object('role','user','content',message)"
            " ELSE json_extract(request, '$.message') END AS input,"
            " status,run_id,error,created_at,"
            " json_extract(payload, '$.context.client.id') AS client_id"
            " FROM client_admissions WHERE kind = 'message'"
            " AND origin IN ('client','answer') ORDER BY rowid"
        )
    ]


def enqueue(
    store: Store,
    actor: Actor,
    *,
    key: str,
    session_id: str,
    profile: str,
    text: str,
    origin: str,
    instructions: str | None = None,
    delivery_ids: tuple[int, ...] = (),
) -> str:
    """Persist one immutable native input; replay cannot change its assignment or body."""
    with store.tx(actor) as tx:
        return enqueue_in_transaction(
            tx,
            key=key,
            session_id=session_id,
            profile=profile,
            text=text,
            origin=origin,
            instructions=instructions,
            delivery_ids=delivery_ids,
        )


def enqueue_in_transaction(
    tx: Tx,
    *,
    key: str,
    session_id: str,
    profile: str,
    text: str,
    origin: str,
    instructions: str | None = None,
    delivery_ids: tuple[int, ...] = (),
) -> str:
    op_id = str(uuid5(NAMESPACE_URL, f"research:{profile}:{key}"))
    request = {
        "key": key,
        "session_id": session_id,
        "profile": profile,
        "text": text,
        "instructions": instructions,
        "delivery_ids": list(delivery_ids),
    }
    row = tx.one("SELECT * FROM client_admissions WHERE op_id=?", (op_id,))
    if row is not None:
        if row["request"] != json.dumps(request, sort_keys=True):
            raise StoreError(["execution identity already belongs to a different payload"])
        return op_id
    tx.execute(
        "INSERT INTO client_admissions"
        " (op_id,kind,request,session_id,message,payload,status,created_at,"
        "profile,instructions,origin,native_key,delivery_ids)"
        " VALUES (?,'message',?,?,?,?,'queued',?,?,?,?,?,?)",
        (
            op_id,
            json.dumps(request, sort_keys=True),
            session_id,
            text,
            json.dumps({"input": text, "instructions": instructions, "context": None}),
            tx.at,
            profile,
            instructions,
            origin,
            key,
            json.dumps(list(delivery_ids)),
        ),
    )
    tx.event(
        "execution.queued", op_id, {"session_id": session_id, "profile": profile, "origin": origin}
    )
    return op_id


def reserved_workers(store: Store, exclude_session: str | None = None) -> int:
    return store.one(
        "SELECT COUNT(DISTINCT a.session_id) FROM client_admissions a"
        " JOIN sessions s ON s.id=a.session_id WHERE s.kind='task'"
        " AND (? IS NULL OR a.session_id!=?)"
        " AND (a.status IN ('queued','running')"
        " OR a.resolution_required=1 OR a.execution_uncertain=1)",
        (exclude_session, exclude_session),
    )[0]


def problems(store: Store) -> list[dict]:
    blocked = [
        dict(row)
        for row in store.q(
            "SELECT op_id,profile,error AS reason FROM client_admissions"
            " WHERE resolution_required=1"
        )
    ]
    blocked.extend(
        dict(row)
        for row in store.q(
            "SELECT op_id,profile,state AS reason FROM task_assignments WHERE state!='held'"
        )
    )
    return blocked


def restart_required(store: Store) -> bool:
    return (
        store.one("SELECT 1 FROM client_admissions WHERE execution_uncertain=1 LIMIT 1") is not None
    )
