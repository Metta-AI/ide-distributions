"""Translate the platform's lifecycle envelope into this distribution's sleep command."""

import asyncio
import base64
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import httpx
from application import Operation
from application import response as application_response
from inference import Availability
from pydantic import UUID8, BaseModel, ConfigDict, model_validator
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

log = logging.getLogger(__name__)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID8
    control: Literal["status", "sleep_request"]

    @model_validator(mode="after")
    def positive_epoch(self) -> Self:
        if self.owner_epoch == 0:
            raise ValueError("Lifecycle owner epoch must be positive")
        return self

    @property
    def owner_epoch(self) -> int:
        # UUIDv8's application bits carry xid8; version and variant bits are not part of the epoch.
        return (
            ((self.id.int >> 80) << 16)
            | (((self.id.int >> 64) & 0xFFF) << 4)
            | ((self.id.int >> 58) & 0xF)
        )


class Lifecycle:
    def __init__(self, env: dict[str, str]):
        self.env = env
        self.sleep_task: asyncio.Task[int] | None = None
        self.last_busy = time.monotonic()
        self.owner_file = Path(env["HERMES_HOME"]) / ".lifecycle-owner"
        self.owner_epoch = int(self.owner_file.read_text()) if self.owner_file.exists() else 0

    async def prepare_sleep(self) -> int:
        process = await asyncio.create_subprocess_exec(
            "/opt/workspace-env/bin/research", "workspace", "sleep", env=self.env
        )
        return await process.wait()

    async def status(self, request: ControlRequest, seam: httpx.AsyncClient) -> dict:
        if request.owner_epoch < self.owner_epoch:
            return {
                "ready": False,
                "ready_for_sleep": False,
                "detail": "Lifecycle owner superseded",
            }
        if request.owner_epoch > self.owner_epoch and self.sleep_task is not None:
            # Ownership transfers only after previously admitted local effects have settled.
            # A disconnected caller must not cancel the runtime's accepted sleep operation.
            await asyncio.shield(self.sleep_task)
            if request.owner_epoch < self.owner_epoch:
                return {
                    "ready": False,
                    "ready_for_sleep": False,
                    "detail": "Lifecycle owner superseded",
                }
        if request.owner_epoch != self.owner_epoch:
            pending = self.owner_file.with_suffix(".pending")
            pending.write_text(str(request.owner_epoch))
            os.replace(pending, self.owner_file)
            self.owner_epoch = request.owner_epoch
        response = await seam.get("/status")
        response.raise_for_status()
        if request.owner_epoch != self.owner_epoch:
            return {
                "ready": False,
                "ready_for_sleep": False,
                "detail": "Lifecycle owner superseded",
            }
        activity = response.json()
        if activity["has_work"]:
            self.last_busy = time.monotonic()
        automatic = time.monotonic() - self.last_busy >= 900
        if (request.control == "sleep_request" or automatic) and (
            self.sleep_task is None or (self.sleep_task.done() and self.sleep_task.result() != 0)
        ):
            self.sleep_task = asyncio.create_task(self.prepare_sleep())
        sleeping = self.sleep_task is not None
        complete = sleeping and self.sleep_task.done()
        return {
            "ready": True,
            "ready_for_sleep": complete and self.sleep_task.result() == 0,
            "detail": "Preparing for sleep" if sleeping and not complete else "",
        }


async def reply(
    socket: ClientConnection,
    stream_id: str,
    envelope: dict,
    lifecycle: Lifecycle,
    seam: httpx.AsyncClient,
    gateway: httpx.AsyncClient,
    inference: Availability,
) -> None:
    """Send one request's frames on the shared reply websocket (Metta spec 0150)."""

    @asynccontextmanager
    async def response():
        if "control" in envelope:
            body = await lifecycle.status(ControlRequest.model_validate(envelope), seam)
            yield httpx.Response(200, json=body)
        else:
            async with application_response(
                Operation.model_validate(envelope), seam, gateway, inference
            ) as result:
                yield result

    async with response() as result:
        frame = {
            "request_id": str(UUID(envelope["id"])),
            "seq": 0,
            "stream_id": stream_id,
            "status": result.status_code,
            "content_type": result.headers.get("content-type", "application/json").split(";", 1)[0],
            "data": "",
            "end": False,
        }
        await socket.send(json.dumps(frame))
        frame["stream_id"] = ""
        async for chunk in result.aiter_bytes():
            for offset in range(0, len(chunk), 65536):
                frame.update(
                    seq=frame["seq"] + 1,
                    data=base64.b64encode(chunk[offset : offset + 65536]).decode(),
                )
                await socket.send(json.dumps(frame))
        frame.update(seq=frame["seq"] + 1, data="", end=True)
        await socket.send(json.dumps(frame))


async def serve(
    *,
    platform_url: str,
    workspace_id: str,
    credential: str,
    env: dict[str, str],
    inference: Availability,
    connected: asyncio.Event,
) -> None:
    """One bidirectional transport; durable admission and operation idempotency belong to AIDE."""
    lifecycle = Lifecycle(env)
    root = f"v2/workspaces/{workspace_id}"
    async with (
        httpx.AsyncClient(
            base_url=platform_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {credential}"},
        ) as player,
        httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:8642/p/orchestrator/",
            headers={"Authorization": f"Bearer {env['API_SERVER_KEY']}"},
        ) as gateway,
    ):
        url = player.build_request("GET", root + "/connection").url
        url = url.copy_with(scheme="wss" if url.scheme == "https" else "ws")

        async def answer(socket: ClientConnection, stream_id: str, envelope: dict) -> None:
            (result,) = await asyncio.gather(
                reply(socket, stream_id, envelope, lifecycle, seam, gateway, inference),
                return_exceptions=True,
            )
            if isinstance(result, httpx.TransportError):
                (result,) = await asyncio.gather(
                    socket.send(
                        json.dumps(
                            {
                                "request_id": str(envelope["id"]),
                                "stream_id": stream_id,
                                "aborted": True,
                            }
                        )
                    ),
                    return_exceptions=True,
                )
            if isinstance(result, ConnectionClosed):
                log.info("Reply transport unavailable: %s", type(result).__name__)
            elif isinstance(result, BaseException):
                raise result

        async def session() -> None:
            async with connect(
                str(url), additional_headers={"Authorization": player.headers["Authorization"]}
            ) as socket:
                connected.set()
                running: dict[UUID, asyncio.Task[None]] = {}
                async with asyncio.TaskGroup() as replies:

                    async def receive() -> None:
                        async for message in socket:
                            incoming = json.loads(message)
                            if "envelope" not in incoming:
                                request_id = UUID(incoming["request_id"])
                                if request_id in running:
                                    running[request_id].cancel()
                                continue
                            request_id = UUID(incoming["envelope"]["id"])
                            if len(running) >= 32:
                                await socket.close(code=1013, reason="Too many in-flight requests")
                                return
                            task = replies.create_task(
                                answer(socket, incoming["stream_id"], incoming["envelope"])
                            )
                            running[request_id] = task
                            task.add_done_callback(lambda _, key=request_id: running.pop(key))

                    (closed,) = await asyncio.gather(receive(), return_exceptions=True)
                    # Disconnect stops observation, not work already durably admitted by AIDE.
                    for task in running.values():
                        task.cancel()
            if isinstance(closed, BaseException):
                raise closed

        while True:
            (result,) = await asyncio.gather(session(), return_exceptions=True)
            temporary = isinstance(result, ConnectionClosed | OSError | httpx.TransportError) or (
                isinstance(result, InvalidStatus) and result.response.status_code in {502, 503, 504}
            )
            if isinstance(result, BaseException) and not temporary:
                raise result
            await asyncio.sleep(1)
