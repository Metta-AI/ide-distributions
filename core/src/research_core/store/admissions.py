"""Durable client receipts and queued chat inputs, owned by the workspace."""

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, get_args
from uuid import NAMESPACE_URL, uuid5

from research_core.store.actors import Actor
from research_core.store.db import Store, Tx
from research_core.store.errors import StoreError

Origin = Literal[
    "client", "answer", "assignment", "continuation", "planning", "wake", "watcher", "migration"
]


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
    origin: Origin,
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
    origin: Origin,
    instructions: str | None = None,
    delivery_ids: tuple[int, ...] = (),
) -> str:
    if origin not in get_args(Origin):
        raise StoreError([f"origin must be one of {', '.join(get_args(Origin))}"])
    op_id = str(uuid5(NAMESPACE_URL, f"research:{profile}:{key}"))
    request = {
        "key": key,
        "session_id": session_id,
        "profile": profile,
        "origin": origin,
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
        "profile,origin,native_key,delivery_ids)"
        " VALUES (?,'message',?,?,?,?,'queued',?,?,?,?,?)",
        (
            op_id,
            json.dumps(request, sort_keys=True),
            session_id,
            text,
            json.dumps({"input": text, "instructions": instructions, "context": None}),
            tx.at,
            profile,
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
    return [
        dict(row)
        for row in store.q(
            "SELECT op_id,profile,error AS reason FROM client_admissions"
            " WHERE resolution_required=1"
        )
    ]


def restart_required(store: Store) -> bool:
    return (
        store.one("SELECT 1 FROM client_admissions WHERE execution_uncertain=1 LIMIT 1") is not None
    )


def work_status(store: Store, lease_file: Path | None = None) -> list[dict]:
    """Visible execution obligations, including terminal deliveries with uncertain execution."""
    from research_core import inference

    owner = store.get_meta("coordinator_instance") or "workspace coordinator awaiting startup"
    paused = store.one("SELECT value FROM settings WHERE key='paused'")
    paused = paused is not None and json.loads(paused[0])
    result = []
    for row in store.q("SELECT * FROM client_admissions WHERE kind='message' ORDER BY rowid"):
        phase = row["status"]
        responsible = row["owner_instance"] or owner
        reason = row["error"]
        trigger = None
        if row["execution_uncertain"] or row["resolution_required"]:
            phase = "waiting"
            responsible = f"runtime:{row['runtime_id']}"
            reason = reason or "execution exclusion required"
            trigger = "private runtime namespace replaced; coordinator reconciles evidence"
        elif phase == "queued":
            responsible = owner
            if paused and not row["dispatch_started"]:
                phase, reason, trigger = "waiting", "workspace paused", "workspace resume"
            elif not row["dispatch_started"] and not inference.available(lease_file):
                blocked = inference.blocking_state(lease_file)
                phase, responsible = "waiting", f"runtime:{store.get_meta('runtime_id')}"
                reason = "inference lease unavailable" if blocked is None else blocked.blocked
                trigger = "new inference lease/key, or provider backoff deadline"
            else:
                reason, trigger = "durable admission queued", "coordinator dispatch"
        elif phase == "running":
            responsible = f"native:{row['profile']}:{row['run_id']}"
            reason, trigger = (
                "execution active",
                "native outcome; reconciliation recovers missed facts",
            )
        elif row["advanced_at"] is None:
            responsible = owner
            phase, reason, trigger = (
                "advancing",
                "outcome committed",
                "coordinator applies durable handoff",
            )
        result.append(
            {
                "attempt_id": row["op_id"],
                "session_id": row["session_id"],
                "state": phase,
                "authority": owner,
                "owner": responsible,
                "reason": reason,
                "trigger": trigger,
                "outcome": row["status"],
                "disposition": row["disposition"],
                "successor_id": row["successor_id"],
            }
        )
    return result
