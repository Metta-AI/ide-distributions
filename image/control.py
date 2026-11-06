"""Automatic sleep, and the platform's lifecycle envelope, for this distribution.

The image decides when it is idle: :meth:`Lifecycle.watch` reads the seam's ``has_work`` every
thirty seconds, and after fifteen minutes without work it prepares for sleep
(``research workspace sleep``) and reports that through ``POST /v2/workspaces/{id}/sleep``
(Metta spec 0149). The platform then releases compute or refuses with a typed reason. A failed
report never authorizes anything: an idle sleep that cannot be reported is undone so the
workspace keeps serving, and tried again later. The ``status`` and ``sleep_request`` control
envelopes stay for the platform's legacy poll.
"""

import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

import httpx
from application import Operation
from application import response as application_response
from inference import Availability
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

log = logging.getLogger(__name__)

IDLE_S = 900.0
CHECK_S = 30.0
REPORT_BACKOFF_S = (30.0, 60.0, 120.0, 300.0, 600.0)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    control: Literal["status", "sleep_request"]


class SleepAccepted(BaseModel):
    kind: Literal["accepted"]
    intent_revision: str


class SleepRefused(BaseModel):
    kind: Literal["stale_pod", "automatic_sleep_disabled", "intent_changed"]
    detail: str


class UnknownWorkspace(BaseModel):
    kind: Literal["unknown_workspace"]


class Forbidden(BaseModel):
    """The platform refused this pod's credential (403)."""

    kind: Literal["forbidden"] = "forbidden"


SleepAnswer = TypeAdapter(
    Annotated[SleepAccepted | SleepRefused | UnknownWorkspace, Field(discriminator="kind")]
)


class Lifecycle:
    """Idle timing, sleep preparation and the sleep report for one pod."""

    def __init__(self, env: dict[str, str], *, player: httpx.AsyncClient, pod: str, root: str):
        self.env = env
        self.player = player
        self.pod = pod
        self.sleep_path = root + "/sleep"
        self.last_busy = time.monotonic()
        self.waiting_on: list[str] = []
        self.cycle: asyncio.Task[None] | None = None
        # The platform's ``sleep_request`` (Pause) arrived; it outlives an idle cycle under way.
        self.requested = False
        # Prepared for sleep and not undone: what the legacy poll reads as ``ready_for_sleep``.
        self.prepared = False
        # Why idle reports stopped for this pod's life; a requested sleep still reports.
        self.stopped: str | None = None
        self.failures = 0
        self.next_attempt = 0.0

    async def research(self, command: str) -> int:
        process = await asyncio.create_subprocess_exec(
            "/opt/workspace-env/bin/research", "workspace", command, env=self.env
        )
        return await process.wait()

    async def observe(self, seam: httpx.AsyncClient) -> dict:
        response = await seam.get("/status")
        response.raise_for_status()
        activity = response.json()
        self.waiting_on = activity["waiting_on"]
        if activity["has_work"] or activity["restart_required"]:
            self.last_busy = time.monotonic()
        return activity

    def idle_due(self) -> bool:
        now = time.monotonic()
        return (
            now - self.last_busy >= IDLE_S
            and now >= self.next_attempt
            and self.stopped is None
            and not self.prepared
            and (self.cycle is None or self.cycle.done())
        )

    async def watch(self, seam: httpx.AsyncClient) -> None:
        """Start an idle sleep once the workspace has had no work for :data:`IDLE_S`."""
        while True:
            if self.cycle is not None and self.cycle.done():
                self.cycle.result()  # an unexpected failure ends the control service loudly
            await self.observe(seam)
            if self.idle_due():
                self.cycle = asyncio.create_task(self.sleep())
            await asyncio.sleep(CHECK_S)

    async def sleep(self) -> None:
        """Prepare for sleep, then report it.

        A requested sleep stays prepared and keeps reporting until the platform answers: the
        owner asked for it. An idle one that cannot be reported is undone, so messages are
        served, and tried again after a backoff while the workspace stays idle.
        """
        reason = "requested" if self.requested else "idle"
        if not self.prepared:
            code = await self.research("sleep")
            if code != 0:
                log.warning("Sleep preparation (%s) exited %s; waking", reason, code)
                await self.wake()
                self.backoff()
                return
            self.prepared = True
        intent_retried = False
        while True:
            match await self.report(reason):
                case SleepAccepted(intent_revision=revision):
                    log.info("Sleep (%s) accepted for intent %s", reason, revision)
                    self.failures = 0
                    return
                case SleepRefused(kind="intent_changed") if not intent_retried:
                    intent_retried = True
                    continue
                case SleepRefused(kind="automatic_sleep_disabled", detail=detail):
                    log.info("Sleep (%s) refused: %s; staying up", reason, detail)
                    self.stopped = "automatic_sleep_disabled"
                    await self.wake()
                    return
                case SleepRefused(kind="stale_pod", detail=detail):
                    # A successor pod owns the workspace; this one stays quiet until it stops.
                    log.warning("Sleep (%s) refused: %s", reason, detail)
                    self.stopped = "stale_pod"
                    return
                case UnknownWorkspace():
                    log.warning("Sleep (%s) refused: the workspace is absent or resetting", reason)
                    self.stopped = "unknown_workspace"
                    return
                case Forbidden():
                    log.error("Sleep (%s) report refused this pod's credential; waking", reason)
                    self.stopped = "forbidden"
                    await self.wake()
                    return
            # A transport error, a 5xx, or an intent that changed again.
            intent_retried = False
            if self.requested:
                await asyncio.sleep(self.backoff())
                continue
            await self.wake()
            self.backoff()
            return

    async def report(
        self, reason: str
    ) -> SleepAccepted | SleepRefused | UnknownWorkspace | Forbidden | None:
        """POST the sleep report; ``None`` for a failure worth retrying."""
        (response,) = await asyncio.gather(
            self.player.post(self.sleep_path, json={"pod": self.pod}), return_exceptions=True
        )
        if isinstance(response, httpx.TransportError):
            log.warning("Sleep (%s) report failed: %s", reason, type(response).__name__)
            return None
        if isinstance(response, BaseException):
            raise response
        if response.status_code >= 500:
            log.warning("Sleep (%s) report failed: HTTP %s", reason, response.status_code)
            return None
        if response.status_code == 403:
            return Forbidden()
        if response.status_code in {404, 409}:
            return SleepAnswer.validate_python(response.json()["detail"])
        response.raise_for_status()
        return SleepAnswer.validate_python(response.json())

    async def wake(self) -> None:
        code = await self.research("wake")
        if code != 0:
            raise RuntimeError(f"research workspace wake exited {code}")
        self.prepared = False

    def backoff(self) -> float:
        delay = REPORT_BACKOFF_S[min(self.failures, len(REPORT_BACKOFF_S) - 1)]
        self.failures += 1
        self.next_attempt = time.monotonic() + delay
        log.warning("Next sleep attempt in %.0f s", delay)
        return delay

    async def status(self, request: ControlRequest, seam: httpx.AsyncClient) -> dict:
        activity = await self.observe(seam)
        if activity["restart_required"]:
            return {
                "ready": True,
                "ready_for_sleep": False,
                "detail": "Recovering native execution",
            }
        if request.control == "sleep_request":
            self.requested = True
            if not self.prepared and (self.cycle is None or self.cycle.done()):
                self.cycle = asyncio.create_task(self.sleep())
        if self.cycle is not None and not self.cycle.done() and not self.prepared:
            detail = "Preparing for sleep"
        elif self.waiting_on:
            detail = "Awake for " + ", ".join(self.waiting_on)
        else:
            detail = ""
        return {"ready": True, "ready_for_sleep": self.prepared, "detail": detail}


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
    pod: str,
    env: dict[str, str],
    inference: Availability,
    connected: asyncio.Event,
) -> None:
    """One bidirectional transport; durable admission and operation idempotency belong to AIDE."""
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
        lifecycle = Lifecycle(env, player=player, pod=pod, root=root)
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

        async def transport() -> None:
            while True:
                (result,) = await asyncio.gather(session(), return_exceptions=True)
                temporary = isinstance(
                    result, ConnectionClosed | OSError | httpx.TransportError
                ) or (
                    isinstance(result, InvalidStatus)
                    and result.response.status_code in {502, 503, 504}
                )
                if isinstance(result, BaseException) and not temporary:
                    raise result
                await asyncio.sleep(1)

        # Idle timing runs in the image, independent of whether the platform polls or connects.
        async with asyncio.TaskGroup() as services:
            services.create_task(transport())
            services.create_task(lifecycle.watch(seam))
