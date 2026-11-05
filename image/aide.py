"""The AIDE 1.0.0 backend: the platform relay's JSON-RPC methods over the seam and the gateway.

It runs beside the legacy control connection in every workspace and stays dormant until the
platform points a frontend at it. Nothing here is persisted: the seam's admissions and the
gateway's idempotency keys make each command retry-safe under its AIDE request id, and
notification subscribers are whoever named a session in the last ten minutes.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from inference import Availability
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

log = logging.getLogger(__name__)

MAX_BYTES = 1048576
IDLE_SECONDS = 600
ACTIVE = frozenset({"queued", "running", "waiting"})
TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})
CONTROLS = frozenset({"approval.request", "approval.settled", "clarify.request", "clarify.settled"})
COMMANDS = frozenset({"session.open", "chat.send", "chat.cancel", "task.cancel", "input.respond"})
TASK_STATUSES = {
    "proposed": "proposed",
    "unclaimed": "pending",
    "in-progress": "in_progress",
    "complete": "completed",
    "abandoned": "abandoned",
}


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Closed):
    scope: Literal["workspace", "coworld"]
    coworld: str | None
    league: str | None


class Open(Closed):
    scope: Scope


class Send(Closed):
    session_id: str
    messages: list[dict[str, JsonValue]]
    scope: Scope


class History(Closed):
    session_id: str
    cursor: str | None = None
    limit: int = 50
    scope: Scope


class ChatCancel(Closed):
    session_id: str
    request_id: UUID
    scope: Scope


class StateList(Closed):
    resource: Literal["sessions", "tasks", "goals"]
    scope_id: str | None = None
    cursor: str | None = None
    limit: int = 50
    scope: Scope | None = None


class StateGet(Closed):
    resource: Literal["sessions", "tasks", "goals"]
    id: str
    scope: Scope


class TaskCancel(Closed):
    task_id: str
    scope: Scope


class Approval(Closed):
    kind: Literal["approval"]
    choice: Literal["once", "deny"]


class Clarification(Closed):
    kind: Literal["clarification"]
    answer: str


class Respond(Closed):
    session_id: str
    request_id: UUID
    response: Annotated[Approval | Clarification, Field(discriminator="kind")]
    scope: Scope


class Context(BaseModel):
    league: str | None = None


class SessionRow(BaseModel):
    id: str
    kind: str
    parent: str | None
    started: str
    ended: str | None
    coworld_id: str | None
    session_context: Context | None

    def scope(self) -> Scope | None:
        """The AIDE scope, or ``None`` for a Coworld session without a league (not exposed)."""
        league = self.session_context.league if self.session_context else None
        if self.coworld_id is None and league is None:
            return Scope(scope="workspace", coworld=None, league=None)
        if self.coworld_id is not None and league is not None:
            return Scope(scope="coworld", coworld=self.coworld_id, league=league)
        return None


class Admission(BaseModel):
    op_id: str
    session_id: str
    status: str
    run_id: str | None
    input: dict[str, JsonValue]
    error: str | None
    error_class: str | None


class Records(BaseModel):
    sessions: list[SessionRow]
    admissions: list[Admission]


class Snapshot(BaseModel):
    seq: int
    records: Records


class Waiting(BaseModel):
    detail: str


class TaskRow(BaseModel):
    id: str
    goal_id: int | None
    title: str
    description: str
    status: str
    outcome: str | None
    result: str | None
    for_human: str
    held_by: str | None
    waiting_on: Waiting | None


class GoalRow(BaseModel):
    id: str
    title: str
    description: str
    end_state: str
    status: str
    where_it_stands: str


class BoardRecords(BaseModel):
    tasks: list[TaskRow]
    goals: list[GoalRow]


class Board(BaseModel):
    seq: int
    records: BoardRecords


class Control(BaseModel):
    kind: Literal["approval", "clarify"]
    prompt_id: str
    execution_generation: int
    description: str = ""
    command: str = ""
    question: str = ""
    choices: list[str] = []


class RunState(BaseModel):
    # Only session-authority runs carry prompts; other runs have none to answer here.
    pending_controls: list[Control] = []


class NativeMessage(BaseModel):
    id: int
    role: str
    content: JsonValue = None
    tool_calls: list[JsonValue] | None = None
    tool_call_id: str | None = None
    display_kind: str | None = None


class NativePage(BaseModel):
    data: list[dict[str, JsonValue]]


class RunEvent(BaseModel):
    event: str
    seq: int | None = None
    timestamp: float = 0
    delta: str | None = None


class SeamEvent(BaseModel):
    seq: int
    kind: str
    subject: str
    payload: dict[str, JsonValue]


class Opened(BaseModel):
    session_id: str


class Opening(BaseModel):
    pending: bool


class Resolution(BaseModel):
    status: str


class Pending(Closed):
    request_id: str
    prompt: str
    kind: Literal["approval", "clarification"]
    choices: list[Literal["once", "deny"]] | None = None


class Session(Closed):
    id: str
    parent_session_id: str | None
    started_at: str
    ended_at: str | None
    active_request_ids: list[str]
    pending: list[Pending]
    scope: Scope


class Record(Closed):
    id: str
    session_id: str
    request_id: str | None = None
    sequence: int
    message: dict[str, JsonValue]


class Goal(Closed):
    id: str
    scope_id: str
    title: str
    description: str
    end_state: str
    status: str
    summary: str


class Task(Closed):
    id: str
    scope_id: str
    goal_id: str | None
    session_ids: list[str]
    title: str
    description: str
    summary: str
    status: str
    outcome: str | None
    waiting_on: Waiting | None


class Binding(BaseModel):
    backend_id: UUID
    version: Literal["1.0.0"]
    max_message_bytes: Literal[1048576]


class Bound(BaseModel):
    binding: Binding


class Frame(BaseModel):
    frontend_id: UUID
    message: dict[str, JsonValue]


class Refusal(Exception):
    """An application error the contract names; ``unknown`` marks an unresolved command."""

    def __init__(self, code: str, message: str, *, unknown: bool = False):
        super().__init__(message)
        self.code, self.unknown = code, unknown

    def error(self) -> dict[str, JsonValue]:
        data = {"code": self.code} | ({"outcome": "unknown"} if self.unknown else {})
        return {"code": -32000, "message": str(self), "data": data}


async def events(response: httpx.Response) -> AsyncIterator[str]:
    """Each server-sent event's data; comments and keepalives carry none."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            data.append(line[6:])
        elif not line and data:
            yield "\n".join(data)
            data = []


def fitting(items: list[BaseModel]) -> int:
    """How many leading items fit in one 1 MiB reply beside its envelope and cursor."""
    total = 1024
    for count, item in enumerate(items):
        total += len(item.model_dump_json(exclude_none=True).encode()) + 1
        if total > MAX_BYTES:
            if count == 0:
                raise Refusal("too_large", "One record exceeds the 1 MiB message limit")
            return count
    return len(items)


def record(message: NativeMessage, session_id: str) -> Record | None:
    """A retained native row as an OpenAI message; hidden scaffolding has no record."""
    if message.display_kind == "hidden" or message.role not in {
        "system",
        "user",
        "assistant",
        "tool",
    }:
        return None
    fields = message.model_dump(include={"role", "content", "tool_calls", "tool_call_id"})
    body = {key: value for key, value in fields.items() if key == "content" or value is not None}
    return Record(id=str(message.id), session_id=session_id, sequence=message.id, message=body)


class Subscription:
    def __init__(self, scope: Scope):
        self.scope = scope
        self.frontends: dict[UUID, float] = {}


class Backend:
    def __init__(
        self,
        seam: httpx.AsyncClient,
        gateway: httpx.AsyncClient,
        inference: Availability,
        work: asyncio.TaskGroup,
    ):
        self.seam, self.gateway, self.inference, self.work = seam, gateway, inference, work
        self.epoch, self.revision = str(uuid4()), 0
        self.socket: ClientConnection | None = None
        self.subscribers: dict[str, Subscription] = {}
        self.requests: dict[str, str] = {}  # admission op id -> session id
        self.wakes: dict[str, asyncio.Event] = {}
        self.bridges: dict[str, asyncio.Task[None]] = {}
        self.start: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    # --- native calls -----------------------------------------------------------------------

    async def call(
        self, client: httpx.AsyncClient, method: str, path: str, command: bool = False, **kwargs
    ) -> bytes:
        """A native reply's body, or the refusal its status means."""
        response = await client.request(method, path, timeout=30, **kwargs)
        if response.is_success:
            return response.content
        if "op_id was already used for different input" in response.text:
            raise Refusal("id_reused", "This request id was used for different input")
        code = {404: "not_found", 409: "forbidden"}.get(response.status_code, "unsupported")
        if response.status_code >= 500:
            code = "unavailable"
        raise Refusal(code, response.text, unknown=command and code == "unavailable")

    async def snapshot(self, **params: str) -> Snapshot:
        snapshot = Snapshot.model_validate_json(
            await self.call(self.seam, "GET", "/snapshot", params={"store": "workspace", **params})
        )
        if not self.start.done():
            self.start.set_result(snapshot.seq)
        for admission in snapshot.records.admissions:
            self.requests[admission.op_id] = admission.session_id
        return snapshot

    async def scoped(self, session_id: str, scope: Scope) -> Snapshot:
        """One session's rows; the seam refuses a scope that differs from the stored one."""
        selection = scope.model_dump(include={"coworld", "league"}, exclude_none=True)
        return await self.snapshot(session_id=session_id, **selection)

    async def pending(self, session_id: str, admissions: list[Admission]):
        """The prompts waiting on this session's running chat requests, with their native ids."""
        found = []
        running = {a.run_id for a in admissions if a.session_id == session_id and a.run_id}
        for run_id in sorted(
            run_id
            for run_id in running
            if any(a.run_id == run_id and a.status == "running" for a in admissions)
        ):
            response = await self.gateway.get(f"v1/runs/{quote(run_id, safe='')}", timeout=30)
            if response.status_code == 404:
                continue  # a retired run has no prompts left
            if not response.is_success:
                raise Refusal("unavailable", response.text)
            for control in RunState.model_validate_json(response.content).pending_controls:
                identity = f"aide-input:{session_id}:{run_id}:{control.execution_generation}"
                pending = Pending(
                    request_id=str(uuid5(NAMESPACE_URL, f"{identity}:{control.prompt_id}")),
                    kind="approval" if control.kind == "approval" else "clarification",
                    prompt=control.description or control.command or control.question,
                    choices=[c for c in control.choices if c in {"once", "deny"}]
                    if control.kind == "approval"
                    else None,
                )
                found.append((pending, run_id, control))
        return found

    async def transcript(self, session_id: str, limit: int, offset: int) -> list[dict]:
        """Native messages newest first; empty until the session's native opening completes."""
        opening = await self.call(
            self.seam, "GET", "/session-pending", params={"session_id": session_id}
        )
        if Opening.model_validate_json(opening).pending:
            return []
        page = await self.call(
            self.gateway,
            "GET",
            f"api/sessions/{quote(session_id, safe='')}/messages",
            params={"order": "latest", "limit": limit, "offset": offset},
        )
        return NativePage.model_validate_json(page).data

    async def project(self, row: SessionRow, admissions: list[Admission]) -> Session:
        return Session(
            id=row.id,
            parent_session_id=row.parent,
            started_at=row.started,
            ended_at=row.ended,
            active_request_ids=[
                a.op_id for a in admissions if a.session_id == row.id and a.status in ACTIVE
            ],
            pending=[pending for pending, _, _ in await self.pending(row.id, admissions)],
            scope=row.scope(),
        )

    async def board(self, coworld: str, resource: str) -> tuple[int, list[BaseModel]]:
        board = Board.model_validate_json(
            await self.call(
                self.seam, "GET", "/snapshot", params={"store": "coworld", "coworld": coworld}
            )
        )
        if resource == "goals":
            return board.seq, [
                Goal(
                    id=f"{coworld}:{row.id}",
                    scope_id=coworld,
                    title=row.title,
                    description=row.description,
                    end_state=row.end_state,
                    status=row.status,
                    summary=row.where_it_stands,
                )
                for row in board.records.goals
            ]
        return board.seq, [
            Task(
                id=f"{coworld}:{row.id}",
                scope_id=coworld,
                goal_id=None if row.goal_id is None else f"{coworld}:G-{row.goal_id}",
                session_ids=[row.held_by] if row.held_by else [],
                title=row.title,
                description=row.description,
                summary=row.result or row.for_human,
                status=TASK_STATUSES[row.status],
                outcome=row.outcome,
                waiting_on=row.waiting_on,
            )
            for row in board.records.tasks
        ]

    # --- notifications ----------------------------------------------------------------------

    def touch(self, session_id: str, scope: Scope, frontend_id: UUID) -> None:
        if session_id not in self.subscribers:
            self.subscribers[session_id] = Subscription(scope)
        self.subscribers[session_id].frontends[frontend_id] = time.monotonic()

    async def send(self, socket: ClientConnection, frontend_id: UUID, message: dict) -> None:
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode()) > MAX_BYTES:
            if "id" not in message:
                log.warning("AIDE notification over 1 MiB dropped: %s", message["method"])
                return
            refusal = Refusal("too_large", "The result exceeds the 1 MiB message limit")
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": refusal.error()})
        try:
            await socket.send(f'{{"frontend_id":"{frontend_id}","message":{body}}}')
        except ConnectionClosed:  # the relay connection ended; it reports the outcome as unknown
            log.info("AIDE delivery lost with its connection")

    async def notify(self, session_id: str, method: str, params: BaseModel | dict) -> None:
        if self.socket is None or session_id not in self.subscribers:
            return
        frontends = self.subscribers[session_id].frontends
        for frontend_id, seen in list(frontends.items()):
            if time.monotonic() - seen > IDLE_SECONDS:
                del frontends[frontend_id]
        if isinstance(params, BaseModel):
            params = params.model_dump(mode="json", exclude_none=True)
        for frontend_id in list(frontends):
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            await self.send(self.socket, frontend_id, message)

    async def changed(self, session_id: str) -> None:
        if session_id not in self.subscribers or not self.subscribers[session_id].frontends:
            return
        snapshot = await self.scoped(session_id, self.subscribers[session_id].scope)
        (row,) = snapshot.records.sessions
        session = await self.project(row, snapshot.records.admissions)
        self.revision += 1
        change = {
            "resource": "sessions",
            "epoch": self.epoch,
            "previous_revision": self.revision - 1,
            "revision": self.revision,
            "change": "upsert",
            "item": session.model_dump(mode="json"),
        }
        await self.notify(session_id, "state.changed", change)

    async def watch(self) -> None:
        """Follow the workspace store's events from the first snapshot this backend served."""
        after = await self.start
        while True:
            try:
                async with self.seam.stream(
                    "GET",
                    "/stream",
                    params={"store": "workspace", "after": after},
                    timeout=httpx.Timeout(10, read=None),
                ) as response:
                    response.raise_for_status()
                    async for data in events(response):
                        event = SeamEvent.model_validate_json(data)
                        after = event.seq
                        if event.kind == "client.accepted":
                            self.requests[event.subject] = str(event.payload["session_id"])
                        if event.subject in self.wakes:
                            self.wakes[event.subject].set()
                        if event.kind.startswith("session."):
                            await self.changed(event.subject)
                        elif event.kind.startswith("client.") and event.subject in self.requests:
                            await self.changed(self.requests[event.subject])
            except httpx.TransportError:  # the seam stream dropped; resume after the last event
                log.info("Seam event stream dropped; resuming after %s", after)
            await asyncio.sleep(1)

    async def publish(self, request_id: str, session_id: str, after: int) -> int:
        """Announce native messages newer than ``after``; return the newest announced id."""
        fresh: list[dict] = []
        offset = 0
        while True:
            page = await self.transcript(session_id, 100, offset)
            newer = [m for m in page if NativeMessage.model_validate(m).id > after]
            fresh = list(reversed(newer)) + fresh
            if len(newer) < 100:
                break
            offset += 100
        if not fresh:
            return after
        annotated = await self.call(
            self.seam, "POST", "/messages/context", json={"session_id": session_id, "data": fresh}
        )
        for message in NativePage.model_validate_json(annotated).data:
            native = NativeMessage.model_validate(message)
            visible = record(native, session_id)
            if visible is not None:
                visible.request_id = request_id
                await self.notify(session_id, "chat.message", visible)
            after = native.id
        return after

    async def follow(self, request_id: str, session_id: str, run_id: str, after: int) -> int:
        """Relay one run's resumable gateway stream until it ends; return the newest message id."""
        last_seq, completion, seq = -1, 0, 0
        while True:
            progressed = False
            try:
                async with self.gateway.stream(
                    "GET",
                    f"v1/runs/{quote(run_id, safe='')}/events",
                    params={"last_seq": last_seq},
                    timeout=httpx.Timeout(30, read=None),
                ) as response:
                    if response.status_code == 404:
                        return after  # retired: its messages are in history
                    response.raise_for_status()
                    async for data in events(response):
                        event = RunEvent.model_validate_json(data)
                        if event.seq is not None:
                            if event.seq <= last_seq:
                                continue
                            last_seq, progressed = event.seq, True
                        if event.event == "message.delta" and event.delta:
                            seq += 1
                            completion_id = f"{run_id}:{completion}"
                            chunk = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": int(event.timestamp),
                                "model": "",
                                "choices": [{"index": 0, "delta": {"content": event.delta}}],
                            }
                            params = {
                                "session_id": session_id,
                                "request_id": request_id,
                                "completion_id": completion_id,
                                "seq": seq,
                                "chunk": chunk,
                            }
                            await self.notify(session_id, "chat.chunk", params)
                        elif event.event == "tool.started":
                            completion, seq = completion + 1, 0
                        elif event.event == "tool.completed":
                            after = await self.publish(request_id, session_id, after)
                        elif event.event in CONTROLS:
                            await self.changed(session_id)
                        elif event.event.removeprefix("run.") in TERMINAL:
                            return after
            except httpx.TransportError:  # the gateway stream dropped; resume after last_seq
                progressed = True
                await asyncio.sleep(1)
            if not progressed:
                return after  # a closed stream with nothing new will never finish this run

    async def bridge(self, request_id: str, session_id: str, scope: Scope, after: int) -> None:
        """Relay one accepted chat request's runs, then its outcome once the seam settles it."""
        wake = self.wakes[request_id]
        followed: set[str] = set()
        try:
            while True:
                wake.clear()
                snapshot = await self.scoped(session_id, scope)
                (admission,) = [a for a in snapshot.records.admissions if a.op_id == request_id]
                if admission.run_id is not None and admission.run_id not in followed:
                    followed.add(admission.run_id)
                    after = await self.follow(request_id, session_id, admission.run_id, after)
                    continue
                if admission.status in TERMINAL:
                    if followed:
                        await self.publish(request_id, session_id, after)
                    finished = {"session_id": session_id, "request_id": request_id}
                    if admission.status in {"completed", "cancelled"}:
                        finished["status"] = admission.status
                    else:
                        error = {
                            "code": admission.error_class or admission.status,
                            "message": admission.error or admission.status,
                        }
                        finished |= {"status": "failed", "error": error}
                    await self.notify(session_id, "chat.finished", finished)
                    return
                await wake.wait()
        finally:
            del self.wakes[request_id]

    # --- methods ------------------------------------------------------------------------------

    async def open(self, frontend_id: UUID, request_id: str, params: Open) -> dict:
        body = {
            "op_id": request_id,
            "client": {"id": "aide", "kind": "ide"},
            "actor": "user:aide",
            "session_context": {"contract_version": 1, **params.scope.model_dump()},
        }
        opened = Opened.model_validate_json(
            await self.call(self.seam, "POST", "/session-open", command=True, json=body)
        )
        self.touch(opened.session_id, params.scope, frontend_id)
        snapshot = await self.scoped(opened.session_id, params.scope)
        (row,) = snapshot.records.sessions
        session = await self.project(row, snapshot.records.admissions)
        return {"session": session.model_dump(mode="json")}

    async def send_chat(self, frontend_id: UUID, request_id: str, params: Send) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        if len(params.messages) != 1 or params.messages[0]["role"] != "user":
            raise Refusal("unsupported", "Send exactly one user message")
        snapshot = await self.scoped(params.session_id, params.scope)
        replay = any(a.op_id == request_id for a in snapshot.records.admissions)
        if not replay and not self.inference.available():
            blocking = self.inference.blocking()
            detail = f": {blocking.blocked} until {blocking.until}" if blocking else ""
            raise Refusal("unavailable", f"Model inference is unavailable{detail}")
        newest = await self.transcript(params.session_id, 1, 0)
        after = NativeMessage.model_validate(newest[0]).id if newest else 0
        self.requests[request_id] = params.session_id
        if request_id not in self.wakes:
            self.wakes[request_id] = asyncio.Event()
        body = {
            "op_id": request_id,
            "session_id": params.session_id,
            "message": params.messages[0],
            "actor": "user:aide",
        }
        await self.call(self.seam, "POST", "/message", command=True, json=body)
        if request_id not in self.bridges:
            bridge = self.bridge(request_id, params.session_id, params.scope, after)
            self.bridges[request_id] = self.work.create_task(bridge)
            self.bridges[request_id].add_done_callback(lambda _: self.bridges.pop(request_id))
        return {"accepted": True, "session_id": params.session_id, "request_id": request_id}

    async def history(self, frontend_id: UUID, request_id: str, params: History) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        snapshot = await self.scoped(params.session_id, params.scope)
        if params.cursor is not None and not params.cursor.isdigit():
            raise Refusal("snapshot_expired", "History cursor is not one this backend issued")
        offset = int(params.cursor or 0)
        native = await self.transcript(params.session_id, params.limit, offset)
        annotated = await self.call(
            self.seam,
            "POST",
            "/messages/context",
            json={"session_id": params.session_id, "data": native},
        )
        rows = [
            NativeMessage.model_validate(m) for m in NativePage.model_validate_json(annotated).data
        ]
        # Newest first, each beside the native offset that follows it. Accepted messages the
        # dispatcher has not yet handed to the gateway lead the first page.
        queued = (
            []
            if params.cursor is not None
            else [
                a for a in snapshot.records.admissions if a.status == "queued" and a.run_id is None
            ]
        )
        top = rows[0].id if rows else 0
        items = [
            (
                Record(
                    id=a.op_id,
                    session_id=params.session_id,
                    request_id=a.op_id,
                    sequence=top + len(queued) - i,
                    message=a.input,
                ),
                offset,
            )
            for i, a in enumerate(reversed(queued))
        ]
        for i, message in enumerate(rows):
            visible = record(message, params.session_id)
            if visible is not None:
                items.append((visible, offset + i + 1))
        page = items[: fitting([item for item, _ in items])]
        following = page[-1][1] if len(page) < len(items) else offset + len(native)
        more = len(page) < len(items) or len(native) == params.limit
        return {
            "items": [item.model_dump(mode="json", exclude_none=True) for item, _ in page[::-1]],
            "next_cursor": str(following) if more else None,
        }

    async def cancel_chat(self, frontend_id: UUID, request_id: str, params: ChatCancel) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        snapshot = await self.scoped(params.session_id, params.scope)
        target = [a for a in snapshot.records.admissions if a.op_id == str(params.request_id)]
        if not target:
            raise Refusal("not_found", "No such chat request in this session")
        body = {"op_id": str(params.request_id), "actor": "user:aide"}
        await self.call(self.seam, "POST", "/message-cancel", command=True, json=body)
        return {"requested": target[0].status in ACTIVE}

    async def list_state(self, frontend_id: UUID, request_id: str, params: StateList) -> dict:
        if params.resource == "sessions":
            snapshot = await self.snapshot()
            revision = self.revision
            rows = [
                row
                for row in snapshot.records.sessions
                if row.kind == "chat"
                and row.parent is None
                and row.scope() is not None
                and (params.scope is None or row.scope() == params.scope)
            ]
        else:
            coworld = params.scope.coworld if params.scope else params.scope_id
            if coworld is None:
                raise Refusal("unsupported", "Tasks and goals are listed per Coworld")
            revision, rows = await self.board(coworld, params.resource)
        selection = params.model_dump_json(include={"resource", "scope_id", "scope"})
        key = hashlib.sha256(selection.encode()).hexdigest()[:16]
        offset = 0
        if params.cursor is not None:
            parts = params.cursor.split(":")
            if len(parts) != 3 or parts[0] != str(revision) or parts[2] != key:
                raise Refusal("snapshot_expired", "The listing changed; read it again")
            offset = int(parts[1])
        page = rows[offset : offset + params.limit]
        if params.resource == "sessions":
            page = [await self.project(row, snapshot.records.admissions) for row in page]
        count = fitting(page)
        following = offset + count
        return {
            "resource": params.resource,
            "epoch": self.epoch if params.resource == "sessions" else "coworld",
            "revision": revision,
            "items": [item.model_dump(mode="json") for item in page[:count]],
            "next_cursor": f"{revision}:{following}:{key}" if following < len(rows) else None,
        }

    async def get_state(self, frontend_id: UUID, request_id: str, params: StateGet) -> dict:
        if params.resource == "sessions":
            self.touch(params.id, params.scope, frontend_id)
            snapshot = await self.scoped(params.id, params.scope)
            (row,) = snapshot.records.sessions
            item, revision = await self.project(row, snapshot.records.admissions), self.revision
        else:
            if params.scope.coworld is None:
                raise Refusal("unsupported", "Tasks and goals belong to a Coworld")
            revision, items = await self.board(params.scope.coworld, params.resource)
            found = [item for item in items if item.id == params.id]
            if not found:
                raise Refusal("not_found", f"No such {params.resource[:-1]}")
            (item,) = found
        epoch = self.epoch if params.resource == "sessions" else "coworld"
        result = {"resource": params.resource, "epoch": epoch, "revision": revision}
        return result | {"item": item.model_dump(mode="json")}

    async def cancel_task(self, frontend_id: UUID, request_id: str, params: TaskCancel) -> dict:
        coworld = params.scope.coworld
        if coworld is None or not params.task_id.startswith(f"{coworld}:"):
            raise Refusal("forbidden", "The task belongs to another scope")
        _, tasks = await self.board(coworld, "tasks")
        found = [task for task in tasks if task.id == params.task_id]
        if not found:
            raise Refusal("not_found", "No such task")
        if found[0].status in {"completed", "abandoned"}:
            return {"requested": False}
        body = {
            "actor": "user:aide",
            "op": "tasks.abandon",
            "coworld": coworld,
            "store": "coworld",
            "args": {
                "task_id": params.task_id.removeprefix(f"{coworld}:"),
                "failure": {"what_failed": "Cancelled by user", "next_step": "None requested"},
            },
        }
        await self.call(self.seam, "POST", "/store/apply", command=True, json=body)
        return {"requested": True}

    async def respond(self, frontend_id: UUID, request_id: str, params: Respond) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        snapshot = await self.scoped(params.session_id, params.scope)
        found = [
            (run_id, control)
            for pending, run_id, control in await self.pending(
                params.session_id, snapshot.records.admissions
            )
            if pending.request_id == str(params.request_id) and pending.kind == params.response.kind
        ]
        if not found:
            raise Refusal("conflict", "That input is no longer pending")
        ((run_id, control),) = found
        answer = params.response.model_dump(exclude={"kind"})
        action = "approval" if control.kind == "approval" else "clarify"
        response = await self.gateway.post(
            f"v1/runs/{quote(run_id, safe='')}/{action}",
            json={
                "request_id": control.prompt_id,
                "execution_generation": control.execution_generation,
                **answer,
            },
            headers={"Idempotency-Key": request_id},
            timeout=30,
        )
        if response.status_code in {400, 404, 409} or (
            response.is_success
            and Resolution.model_validate_json(response.content).status != "resolved"
        ):
            raise Refusal("conflict", "That input is no longer pending")
        if not response.is_success:
            raise Refusal("unavailable", response.text, unknown=True)
        return {"applied": True}

    # --- the connection -----------------------------------------------------------------------

    async def answer(self, socket: ClientConnection, frame: Frame) -> None:
        message = frame.message
        request_id, method = str(message["id"]), str(message["method"])
        handlers = {
            "session.open": (Open, self.open),
            "chat.send": (Send, self.send_chat),
            "chat.history": (History, self.history),
            "chat.cancel": (ChatCancel, self.cancel_chat),
            "state.list": (StateList, self.list_state),
            "state.get": (StateGet, self.get_state),
            "task.cancel": (TaskCancel, self.cancel_task),
            "input.respond": (Respond, self.respond),
        }
        reply: dict[str, JsonValue] = {"jsonrpc": "2.0", "id": request_id}
        if method not in handlers:
            reply["error"] = Refusal("unsupported", f"{method} is not supported").error()
            await self.send(socket, frame.frontend_id, reply)
            return
        model, handler = handlers[method]
        try:
            params = model.model_validate(message["params"])
        except ValidationError as error:  # the relay checked the contract; these are our limits
            reply["error"] = {"code": -32602, "message": str(error)}
            await self.send(socket, frame.frontend_id, reply)
            return
        try:
            reply["result"] = await handler(frame.frontend_id, request_id, params)
        except Refusal as refusal:  # the contract's typed application errors
            reply["error"] = refusal.error()
        except httpx.TransportError as error:  # the seam or gateway dropped mid-request
            unknown = method in COMMANDS
            refusal = Refusal("unavailable", type(error).__name__, unknown=unknown)
            reply["error"] = refusal.error()
        await self.send(socket, frame.frontend_id, reply)

    async def connect(self, url: str, credential: str) -> None:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {credential}"},
            subprotocols=["aide.1.0.0"],
            max_size=2 * MAX_BYTES,
        ) as socket:
            Bound.model_validate_json(await socket.recv())
            self.socket = socket
            try:
                async with asyncio.TaskGroup() as replies:
                    try:
                        async for raw in socket:
                            frame = Frame.model_validate_json(raw)
                            # Only requests need answers; this backend sends no reverse requests.
                            if "method" in frame.message and "id" in frame.message:
                                replies.create_task(self.answer(socket, frame))
                    except ConnectionClosed:  # superseded or expired; in-flight commands finish
                        log.info("AIDE relay closed the connection")
            finally:
                self.socket = None


async def serve(
    *,
    platform_url: str,
    workspace_id: str,
    credential: str,
    env: dict[str, str],
    inference: Availability,
) -> None:
    """Hold the platform's AIDE backend socket forever; failures only delay reconnection."""
    url = f"{platform_url.rstrip('/')}/v2/workspaces/{workspace_id}/aide/1.0.0/backend"
    url = "ws" + url.removeprefix("http")
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:8642/p/orchestrator/",
            headers={"Authorization": f"Bearer {env['API_SERVER_KEY']}"},
        ) as gateway,
        asyncio.TaskGroup() as work,
    ):
        backend = Backend(seam, gateway, inference, work)
        work.create_task(backend.watch())
        delay = 1
        while True:
            started = time.monotonic()
            try:
                await backend.connect(url, credential)
            except (ConnectionClosed, InvalidHandshake, OSError) as error:  # retry forever
                log.info("AIDE relay connection ended: %s", type(error).__name__)
            delay = 1 if time.monotonic() - started > 60 else min(delay * 2, 30)
            await asyncio.sleep(delay)
