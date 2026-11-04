"""The distribution's application interface; hosting only transports these envelopes."""

import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from inference import Availability
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from contract import (
    NAMES,
    RESPONSES,
    Approval,
    Cancel,
    Clarification,
    Events,
    InferenceUnavailable,
    Message,
    Messages,
    OpenSession,
    Request,
    Run,
    Snapshot,
)

log = logging.getLogger(__name__)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    op_id: UUID
    name: str
    args: dict[str, JsonValue]


class PendingSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: bool


@asynccontextmanager
async def response(
    operation: Operation,
    seam: httpx.AsyncClient,
    gateway: httpx.AsyncClient,
    inference: Availability,
):
    """Forward native replies, including the gateway's resumable run event stream."""
    if operation.name not in NAMES:
        yield httpx.Response(501, json={"kind": "unsupported", "name": operation.name})
        return
    # Invalid client arguments are refusals, not failures of the workspace process.
    import asyncio

    async def validate():
        return Request.model_validate(operation.model_dump(exclude={"id"})).root.args

    (args,) = await asyncio.gather(validate(), return_exceptions=True)
    if isinstance(args, ValidationError):
        yield httpx.Response(422, json={"kind": "invalid", "detail": str(args)})
        return
    if isinstance(args, BaseException):
        raise args
    if isinstance(args, Message) and not inference.available():
        blocking = inference.blocking()
        refusal = InferenceUnavailable(
            reason=blocking.blocked if blocking else None,
            until=blocking.until if blocking else None,
        )
        yield httpx.Response(503, json=refusal.model_dump(mode="json", exclude={"detail"}))
        return
    if isinstance(args, Messages | Message):
        selected = await seam.get(
            "/snapshot",
            params={
                "store": "workspace",
                **args.model_dump(include={"session_id", "coworld", "league"}, exclude_none=True),
            },
            timeout=10,
        )
        if selected.status_code != 200:
            yield selected
            return
    if isinstance(args, Messages):
        opening = await seam.get(
            "/session-pending", params={"session_id": args.session_id}, timeout=10
        )
        opening.raise_for_status()
        if PendingSession.model_validate_json(opening.content).pending:
            yield httpx.Response(200, json={"data": []})
            return
    client = gateway
    method = "GET"
    headers = {"Idempotency-Key": str(operation.op_id)}
    params = {}
    body = None
    match args:
        case Snapshot():
            client, path, params = seam, "/snapshot", args.model_dump(exclude_none=True)
        case OpenSession() | Message():
            client = seam
            path = "/session-open" if isinstance(args, OpenSession) else "/message"
            method = "POST"
            body = args.model_dump(
                mode="json", exclude_none=True, exclude={"coworld", "league", "message_op_id"}
            ) | {
                "op_id": str(operation.op_id),
                "actor": "user:ide-next",
            }
            if isinstance(args, OpenSession):
                body["session_context"] = {
                    **(args.session_context or {}),
                    "contract_version": 1,
                    "scope": "coworld" if args.coworld else "workspace",
                    "coworld": args.coworld,
                    "league": args.league,
                }
                if args.message_op_id is not None:
                    body["op_id"] = str(uuid5(NAMESPACE_URL, f"session-open:{args.message_op_id}"))
        case Cancel():
            client, path, method = seam, "/message-cancel", "POST"
            body = {"op_id": str(args.op_id), "actor": "user:ide-next"}
        case Messages():
            path = f"api/sessions/{quote(args.session_id, safe='')}/messages"
            params = {"order": "latest", "limit": args.limit, "offset": args.offset}
        case Events():
            path = f"v1/runs/{quote(args.run_id, safe='')}/events"
            params = {"last_seq": args.last_seq}
        case Approval() | Clarification():
            path = f"v1/runs/{quote(args.run_id, safe='')}/" + (
                "approval" if isinstance(args, Approval) else "clarify"
            )
            method = "POST"
            body = args.model_dump(exclude={"run_id"}, exclude_none=True)
        case Run():
            path = f"v1/runs/{quote(args.run_id, safe='')}"
            if operation.name == "run.stop":
                path += "/stop"
                method = "POST"
        case _:
            raise AssertionError(type(args))
    started = time.monotonic()
    log.info(
        "Workspace service request started: request_id=%s op_id=%s operation=%s path=%s",
        operation.id,
        operation.op_id,
        operation.name,
        path,
    )
    async with client.stream(
        method,
        path,
        params=params,
        json=body,
        headers=headers,
        timeout=httpx.Timeout(30, read=None) if isinstance(args, Events) else 30,
    ) as result:
        log.info(
            "Workspace service headers: request_id=%s seconds=%.3f status=%s",
            operation.id,
            time.monotonic() - started,
            result.status_code,
        )
        if 200 <= result.status_code < 300 and not isinstance(args, Events):
            reading = time.monotonic()
            payload = await result.aread()
            RESPONSES[operation.name].model_validate_json(payload)
            log.info(
                "Workspace service body validated: request_id=%s seconds=%.3f bytes=%s",
                operation.id,
                time.monotonic() - reading,
                len(payload),
            )
            if isinstance(args, Messages):
                annotating = time.monotonic()
                log.info("Workspace history annotation started: request_id=%s", operation.id)
                annotated = await seam.post(
                    "/messages/context",
                    json={
                        "session_id": args.session_id,
                        "data": result.json()["data"],
                    },
                    timeout=10,
                )
                log.info(
                    "Workspace history annotated: request_id=%s seconds=%.3f status=%s",
                    operation.id,
                    time.monotonic() - annotating,
                    annotated.status_code,
                )
                yield annotated
                return
        yield result
