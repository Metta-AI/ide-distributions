"""The distribution's application interface; hosting only transports these envelopes."""

from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from inference import Availability
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    op_id: UUID
    name: str
    args: dict[str, JsonValue]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Snapshot(Arguments):
    store: Literal["workspace", "coworld"]


class Messages(Arguments):
    session_id: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class Run(Arguments):
    run_id: str = Field(min_length=1, max_length=200)


class Events(Run):
    last_seq: int = Field(default=0, ge=0)


class Approval(Run):
    request_id: str
    choice: Literal["once", "deny"]


class Client(Arguments):
    id: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=40)


class OpenSession(Arguments):
    client: Client
    session_context: dict[str, JsonValue] | None = None
    capabilities: dict[str, JsonValue] | None = None


class Message(Arguments):
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=16000)
    context: dict[str, JsonValue]


class Cancel(Arguments):
    op_id: UUID


MODELS = {
    "workspace.snapshot": Snapshot,
    "session.open": OpenSession,
    "session.message": Message,
    "session.cancel": Cancel,
    "session.messages": Messages,
    "run.get": Run,
    "run.events": Events,
    "run.stop": Run,
    "run.approve": Approval,
}


@asynccontextmanager
async def response(
    operation: Operation,
    seam: httpx.AsyncClient,
    gateway: httpx.AsyncClient,
    inference: Availability,
):
    """Forward native replies, including the gateway's resumable run event stream."""
    if operation.name not in MODELS:
        yield httpx.Response(501, json={"kind": "unsupported", "name": operation.name})
        return
    # Invalid client arguments are refusals, not failures of the workspace process.
    import asyncio

    async def validate():
        return MODELS[operation.name].model_validate(operation.args)

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
        method, path, params=params, json=body, headers=headers,
        timeout=httpx.Timeout(30, read=None) if isinstance(args, Events) else 30,
    ) as result:
        yield result
