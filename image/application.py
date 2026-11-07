"""The distribution's application interface; hosting only transports these envelopes."""

from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from inference import Availability
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

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
from research_core.agent_telemetry import Producer, TelemetryWriter, stream_path
from research_core.telemetry import propagator, span

_telemetry: TelemetryWriter | None = None


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    op_id: UUID
    name: str
    args: dict[str, JsonValue]
    trace_context: dict[str, str] = Field(default_factory=dict)


class PendingSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pending: bool


def _record_refusal(
    operation: Operation, status: int, reason: str, session_id: str | None = None
) -> None:
    """A request the seam will never see, recorded here so that it reaches the agent's records.

    A name we do not know is a client's free text, so it stays out.
    """
    global _telemetry
    if _telemetry is None:
        _telemetry = TelemetryWriter(stream_path(), Producer.for_process("bridge"))
    _telemetry.capture_operation(
        "admission",
        "refused",
        request_id=str(operation.id),
        operation_id=str(operation.op_id),
        admission_id=str(operation.op_id),
        native_event_type=operation.name if operation.name in NAMES else "unknown",
        session_id=session_id,
        reason=reason,
        details={"status": status},
    )


@asynccontextmanager
async def response(
    operation: Operation,
    seam: httpx.AsyncClient,
    gateway: httpx.AsyncClient,
    inference: Availability,
):
    """Forward native replies, including the gateway's resumable run event stream."""
    if operation.name not in NAMES:
        _record_refusal(operation, 501, "unsupported")
        yield httpx.Response(501, json={"kind": "unsupported", "name": operation.name})
        return
    # Invalid client arguments are refusals, not failures of the workspace process.
    import asyncio

    async def validate():
        return Request.model_validate(
            operation.model_dump(exclude={"id", "trace_context"})
        ).root.args

    (args,) = await asyncio.gather(validate(), return_exceptions=True)
    if isinstance(args, ValidationError):
        _record_refusal(operation, 422, "invalid")
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
        _record_refusal(operation, 503, "inference_unavailable", args.session_id)
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
            # The selection failed, so this ``session_id`` names no Session.
            _record_refusal(operation, selected.status_code, "session_selection")
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
    if client is seam:
        headers["X-Bridge-Request-Id"] = str(operation.id)
    with span(
        "workspace.request",
        context=propagator.extract(operation.trace_context),
        **{
            "request.id": str(operation.id),
            "op.id": str(operation.op_id),
            "operation": operation.name,
        },
    ) as current:
        propagator.inject(headers)
        async with client.stream(
            method,
            path,
            params=params,
            json=body,
            headers=headers,
            timeout=httpx.Timeout(30, read=None) if isinstance(args, Events) else 30,
        ) as result:
            current.set_attribute("http.response.status_code", result.status_code)
            if result.status_code >= 400:
                current.set_status(StatusCode.ERROR)
            current.add_event("response.headers")
            if 200 <= result.status_code < 300 and not isinstance(args, Events):
                with span("workspace.response.validate") as validation:
                    payload = await result.aread()
                    RESPONSES[operation.name].model_validate_json(payload)
                    validation.set_attribute("http.response.body.size", len(payload))
                if isinstance(args, Messages):
                    with span("workspace.history.annotate") as annotation:
                        annotation_headers = {}
                        propagator.inject(annotation_headers)
                        annotated = await seam.post(
                            "/messages/context",
                            json={"session_id": args.session_id, "data": result.json()["data"]},
                            headers=annotation_headers,
                            timeout=10,
                        )
                        annotation.set_attribute("http.response.status_code", annotated.status_code)
                        if annotated.status_code >= 400:
                            annotation.set_status(StatusCode.ERROR)
                    yield annotated
                    return
            yield result
