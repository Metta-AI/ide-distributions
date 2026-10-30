"""Translate the platform's lifecycle envelope into this distribution's sleep command."""

import asyncio
import base64
import json
import logging
import time
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    control: Literal["status", "sleep_request"]


class Lifecycle:
    def __init__(self, env: dict[str, str]):
        self.env = env
        self.sleep_task: asyncio.Task[int] | None = None
        self.last_busy = time.monotonic()

    async def prepare_sleep(self) -> int:
        process = await asyncio.create_subprocess_exec(
            "/opt/workspace-env/bin/research", "workspace", "sleep", env=self.env
        )
        return await process.wait()

    async def status(self, request: ControlRequest, seam: httpx.AsyncClient) -> dict:
        response = await seam.get("/status")
        response.raise_for_status()
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
    player: httpx.AsyncClient,
    root: str,
    envelope: dict,
    lifecycle: Lifecycle,
    seam: httpx.AsyncClient,
) -> None:
    request_id = str(UUID(envelope["id"]))
    if "control" in envelope:
        body = await lifecycle.status(ControlRequest.model_validate(envelope), seam)
        status = 200
    else:
        # Never acknowledge unsupported application work.
        status = 501
        body = {"kind": "unsupported", "name": envelope["name"]}
    url = player.build_request("GET", root + f"/replies/{request_id}").url
    url = url.copy_with(scheme="wss" if url.scheme == "https" else "ws")
    async with connect(
        str(url), additional_headers={"Authorization": player.headers["Authorization"]}
    ) as socket:
        frame = {
            "connection_id": request_id,
            "seq": 0,
            "status": status,
            "content_type": "application/json",
            "data": "",
            "end": False,
        }
        await socket.send(json.dumps(frame))
        if await socket.recv() != "accepted":
            return
        frame.update(seq=1, data=base64.b64encode(json.dumps(body).encode()).decode())
        await socket.send(json.dumps(frame))
        if await socket.recv() != "accepted":
            return
        frame.update(seq=2, data="", end=True)
        await socket.send(json.dumps(frame))
        await socket.recv()


async def serve(
    *, platform_url: str, workspace_id: str, credential: str, env: dict[str, str]
) -> None:
    lifecycle = Lifecycle(env)
    root = f"v2/workspaces/{workspace_id}"
    async with (
        httpx.AsyncClient(
            base_url=platform_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {credential}"},
        ) as player,
        httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam,
    ):

        async def receive() -> None:
            async with player.stream("GET", root + "/connection", timeout=45) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        result = (
                            await asyncio.gather(
                                reply(player, root, json.loads(line), lifecycle, seam),
                                return_exceptions=True,
                            )
                        )[0]
                        if isinstance(result, ConnectionClosed):
                            log.warning(
                                "Lifecycle reply connection closed; the next poll can retry"
                            )
                        elif isinstance(result, BaseException):
                            raise result

        while True:
            result = (await asyncio.gather(receive(), return_exceptions=True))[0]
            if isinstance(result, BaseException) and not isinstance(result, httpx.TransportError):
                raise result
            await asyncio.sleep(1)
