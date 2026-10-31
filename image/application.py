"""The distribution's application interface; hosting only transports these envelopes."""

from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import UUID

import httpx
from inference import Availability
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from contract import (
    NAMES,
    RESPONSES,
    Approval,
    Cancel,
    Events,
    Message,
    Messages,
    OpenSession,
    Request,
    Run,
    Snapshot,
)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    op_id: UUID
    name: str
    args: dict[str, JsonValue]


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
        yield httpx.Response(503, json={"kind": "inference_unavailable"})
        return
    client = gateway
    method = "GET"
    headers = {"Idempotency-Key": str(operation.op_id)}
    params = {}
    body = None
    match args:
        case Snapshot():
            client, path, params = seam, "/snapshot", {"store": args.store}
        case OpenSession() | Message():
            client = seam
            path = "/session-open" if isinstance(args, OpenSession) else "/message"
            method = "POST"
            body = args.model_dump(mode="json", exclude_none=True) | {
                "op_id": str(operation.op_id),
                "actor": "user:ide-next",
            }
        case Cancel():
            client, path, method = seam, "/message-cancel", "POST"
            body = {"op_id": str(args.op_id), "actor": "user:ide-next"}
        case Messages():
            path = f"api/sessions/{quote(args.session_id, safe='')}/messages"
            params = {"order": "latest", "limit": args.limit, "offset": args.offset}
        case Events():
            path = f"v1/runs/{quote(args.run_id, safe='')}/events"
            params = {"last_seq": args.last_seq}
        case Approval():
            path = f"v1/runs/{quote(args.run_id, safe='')}/approval"
            method = "POST"
            body = {"request_id": args.request_id, "choice": args.choice}
        case Run():
            path = f"v1/runs/{quote(args.run_id, safe='')}"
            if operation.name == "run.stop":
                path += "/stop"
                method = "POST"
        case _:
            raise AssertionError(type(args))
    async with client.stream(
        method,
        path,
        params=params,
        json=body,
        headers=headers,
        timeout=httpx.Timeout(30, read=None) if isinstance(args, Events) else 30,
    ) as result:
        if 200 <= result.status_code < 300 and not isinstance(args, Events):
            payload = await result.aread()
            RESPONSES[operation.name].model_validate_json(payload)
        yield result
