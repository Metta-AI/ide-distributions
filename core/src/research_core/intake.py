"""HTTP intake validation, atomic receipts and latency telemetry.

Kept outside the shared admission ledger so in-process native tools stay stdlib-only.
"""

import json
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, NonNegativeFloat

from research_core.store import admissions, sessions
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError


class OpenRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str
    coworld_id: str | None
    session_context: dict | None
    client_capabilities: dict | list | None


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str | None
    replayed: bool
    writer_wait_ms: NonNegativeFloat
    write_ms: NonNegativeFloat
    commit_ms: NonNegativeFloat
    registration_ms: NonNegativeFloat = 0


class IntakeTiming(Receipt):
    event: Literal["workspace_intake"] = "workspace_intake"
    operation: Literal["session.open", "session.message"]
    op_id: str
    validation_ms: NonNegativeFloat
    store_open_ms: NonNegativeFloat
    metadata_ms: NonNegativeFloat
    total_ms: NonNegativeFloat

    def emit(self) -> None:
        sys.stderr.write(self.model_dump_json() + "\n")
        sys.stderr.flush()


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
    registration: OpenRegistration | None = None,
    canonical_request: dict | None = None,
) -> Receipt:
    started = time.monotonic()
    with store.tx(actor) as tx:
        acquired = time.monotonic()
        command = tx.one("SELECT method FROM aide_command_receipts WHERE request_id=?", (op_id,))
        if command is not None and command["method"] != (
            "session.open" if kind == "open" else "chat.send"
        ):
            raise StoreError(["op_id was already used for different input"])
        if canonical_request is not None:
            identity = tx.one("SELECT * FROM aide_receipts WHERE request_id=?", (op_id,))
            if identity is not None and (
                (kind == "message" and identity["session_id"] != canonical_request["session_id"])
                or json.loads(identity["scope"]) != canonical_request["scope"]
                or json.loads(identity["messages"]) != canonical_request.get("messages", [])
            ):
                raise StoreError(["op_id was already used for different input"])
            existing = (
                store.one("SELECT * FROM client_admissions WHERE op_id=?", (op_id,))
                if identity is not None
                else admissions.read(store, op_id, kind, request)
            )
        else:
            existing = admissions.read(store, op_id, kind, request)
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
            if canonical_request is not None:
                tx.execute(
                    "INSERT INTO aide_receipts(request_id,session_id,scope,messages)"
                    " VALUES (?,?,?,?)",
                    (
                        op_id,
                        session_id,
                        json.dumps(canonical_request["scope"], sort_keys=True),
                        json.dumps(canonical_request.get("messages", []), sort_keys=True),
                    ),
                )
        registering = time.monotonic()
        if registration is not None and existing is None:
            sessions.register_in_transaction(
                tx,
                session_id=existing["session_id"] if existing is not None else session_id,
                kind="chat",
                **registration.model_dump(),
            )
        written = time.monotonic()
    committed = time.monotonic()
    return Receipt(
        session_id=existing["session_id"] if existing is not None else session_id,
        run_id=existing["run_id"] if existing is not None else None,
        replayed=existing is not None,
        writer_wait_ms=(acquired - started) * 1000,
        write_ms=(registering - acquired) * 1000,
        commit_ms=(committed - written) * 1000,
        registration_ms=(written - registering) * 1000 if registration is not None else 0,
    )
