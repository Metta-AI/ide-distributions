"""The AIDE 1.0.0 backend: the platform relay's JSON-RPC methods over the seam and the gateway.

It runs beside the legacy control connection in every workspace and stays dormant until the
platform points a frontend at it. Reads are authoritative and come from durable workspace
state: request records from the seam's admissions and their events, progress from the
gateway's retained run events, messages from the gateway's history. Notifications are a fast
path that carries the cursor of the read returning the same information; the bridges that push
them are rebuilt from those reads whenever the backend (re)connects, so losing them loses
nothing. A frontend is subscribed to a session from its first request naming it until the relay
reports ``frontend.closed``.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from inference import Availability
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_serializer,
)
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from research_core.run_failures import CONTINUATION_PREFIX
from research_core.telemetry import propagator, span

log = logging.getLogger(__name__)

MAX_BYTES = 1048576
ACTIVE = frozenset({"queued", "running", "waiting"})
TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})
CONTROLS = frozenset({"approval.request", "approval.settled", "clarify.request", "clarify.settled"})
COMMANDS = frozenset({"session.open", "chat.send", "chat.cancel", "task.cancel", "input.respond"})
RECENT_TERMINAL = 10  # terminal requests a Session embeds beside its open ones
EMBEDDED_ACTIVE = 1000  # open requests a Session embeds at most
ERROR_CHARS = 4000  # RequestError.message
TEXT_CHARS = 65536  # reasoning, interim text and tool arguments in one event
REPLAY_IDLE_SECONDS = 0.25  # a live run's stream has sent its retained events by then
DEFAULT_CLIENT = {"id": "aide", "kind": "ide"}
TASK_STATUSES = {
    "proposed": "proposed",
    "unclaimed": "pending",
    "in-progress": "in_progress",
    "complete": "completed",
    "abandoned": "abandoned",
}


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Wire(Closed):
    """An AIDE object: optional fields are absent when unset, required nullable ones stay null."""

    @model_serializer(mode="wrap")
    def sparse(self, handler):
        required = {
            field.serialization_alias or name
            for name, field in type(self).model_fields.items()
            if field.is_required()
        }
        return {k: v for k, v in handler(self).items() if v is not None or k in required}


class Scope(Closed):
    scope: Literal["workspace", "coworld"]
    coworld: str | None
    league: str | None


class Cursor(Closed):
    attempt: int = Field(ge=0)
    seq: int = Field(ge=0)


# --- parameters -------------------------------------------------------------------------------
# ``wake`` belongs to the relay, which decides whether a read may wake the workspace; by the time
# a read reaches this backend the workspace is awake, so it is accepted and has no effect here.


class Client(Closed):
    id: str
    kind: str
    guide: str | None = None


class Open(Closed):
    scope: Scope
    client: Client | None = None


class Send(Closed):
    session_id: str
    messages: list[dict[str, JsonValue]]
    scope: Scope
    context: dict[str, JsonValue] | None = None
    instructions: str | None = Field(default=None, max_length=64000)


class History(Closed):
    session_id: str
    cursor: str | None = None
    after_sequence: int | None = Field(default=None, ge=0)
    limit: int = 50
    scope: Scope
    wake: bool = True


class Events(Closed):
    session_id: str
    request_id: UUID
    after: Cursor
    limit: int = Field(default=200, ge=1, le=1000)
    scope: Scope
    wake: bool = True


class ChatCancel(Closed):
    session_id: str
    request_id: UUID
    scope: Scope


class StateList(Closed):
    resource: Literal["sessions", "tasks", "goals", "requests"]
    scope_id: str | None = None
    session_id: str | None = None
    cursor: str | None = None
    limit: int = 50
    scope: Scope | None = None
    wake: bool = True


class StateGet(Closed):
    resource: Literal["sessions", "tasks", "goals", "requests"]
    id: str
    scope: Scope
    wake: bool = True


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


# --- native replies ---------------------------------------------------------------------------


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


class Run(BaseModel):
    attempt: int
    run_id: str


class Placed(BaseModel):
    attempt: int
    seq: int
    at: str


class Admission(BaseModel):
    """One message admission as ``research_core.store.admissions.requests`` reads it."""

    op_id: str
    session_id: str
    status: str
    input: dict[str, JsonValue] | None
    error: str | None
    error_class: str | None
    resume_after: str | None
    created_at: str
    updated_at: str
    revision: int
    resumes: int
    runs: list[Run]
    finished: Placed | None


class Records(BaseModel):
    sessions: list[SessionRow]
    requests: list[Admission]


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
    client_context: dict[str, JsonValue] | None = None
    reasoning: str | None = None
    reasoning_content: str | None = None
    timestamp: float | None = None


class NativePage(BaseModel):
    data: list[dict[str, JsonValue]]


class RunEvent(BaseModel):
    """One gateway run event (``/v1/runs/{id}/events``); ``seq`` is absent on stream markers."""

    event: str
    seq: int | None = None
    timestamp: float = 0
    delta: str | None = None
    text: str | None = None
    already_streamed: bool = False
    tool: str | None = None
    preview: str | None = None
    tool_call_id: str | None = None


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


# --- AIDE objects -----------------------------------------------------------------------------


class ApprovalInput(Wire):
    request_id: str
    prompt: str
    kind: Literal["approval"] = "approval"
    choices: list[Literal["once", "deny"]]
    description: str | None = None
    command: str | None = None


class ClarificationInput(Wire):
    request_id: str
    prompt: str
    kind: Literal["clarification"] = "clarification"


Pending = ApprovalInput | ClarificationInput


class RequestError(Wire):
    model_config = ConfigDict(serialize_by_alias=True)

    error_class: str | None = Field(serialization_alias="class")
    code: str
    message: str


class RequestWaiting(Wire):
    reason: str | None
    until: str | None


class ChatRequest(Wire):
    request_id: str
    session_id: str
    scope: Scope
    origin: Literal["user", "runtime"]
    status: str
    created_at: str
    updated_at: str
    revision: int
    attempt: int
    in_history: bool
    error: RequestError | None
    waiting: RequestWaiting | None
    messages: list[dict[str, JsonValue]] | None = None
    message_omitted: Literal[True] | None = None


class Session(Wire):
    id: str
    parent_session_id: str | None
    started_at: str
    ended_at: str | None
    active_request_ids: list[str]
    pending: list[Pending]
    scope: Scope
    requests: list[ChatRequest]


class Record(Wire):
    id: str
    session_id: str
    request_id: str | None = None
    sequence: int
    message: dict[str, JsonValue]
    kind: Literal["message", "continuation", "failed_turn"]
    reasoning: str | None = None
    created_at: str | None = None


class Goal(Wire):
    id: str
    scope_id: str
    title: str
    description: str
    end_state: str
    status: str
    summary: str


class Task(Wire):
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


class Event(Wire):
    attempt: int
    seq: int
    at: str


class TextDelta(Event):
    kind: Literal["text.delta"] = "text.delta"
    completion_id: str
    delta: str


class TextInterim(Event):
    kind: Literal["text.interim"] = "text.interim"
    text: str
    already_streamed: bool
    truncated: Literal[True] | None = None


class Reasoning(Event):
    kind: Literal["reasoning"] = "reasoning"
    text: str | None = None
    truncated: Literal[True] | None = None


class ToolStarted(Event):
    kind: Literal["tool.started"] = "tool.started"
    call_id: str
    name: str
    arguments: str
    truncated: Literal[True] | None = None


class ToolCompleted(Event):
    kind: Literal["tool.completed"] = "tool.completed"
    call_id: str
    name: str


class Finished(Event):
    kind: Literal["finished"] = "finished"
    status: Literal["completed", "cancelled", "failed", "interrupted"]
    error: RequestError | None = None


ChatEvent = TextDelta | TextInterim | Reasoning | ToolStarted | ToolCompleted | Finished


class Binding(BaseModel):
    backend_id: UUID
    version: Literal["1.0.0"]
    max_message_bytes: Literal[1048576]


class Bound(BaseModel):
    binding: Binding


class Frame(BaseModel):
    frontend_id: UUID
    message: dict[str, JsonValue]
    trace_context: dict[str, str] = Field(default_factory=dict)


class Refusal(Exception):
    """An error the contract names: ``invalid`` is malformed parameters (-32602), every other
    code an application error; ``unknown`` marks an unresolved command."""

    def __init__(self, code: str, message: str, *, unknown: bool = False, **data: JsonValue):
        super().__init__(message)
        self.code, self.unknown, self.data = code, unknown, data

    def error(self) -> dict[str, JsonValue]:
        if self.code == "invalid":
            return {"code": -32602, "message": str(self)}
        data = {"code": self.code} | ({"outcome": "unknown"} if self.unknown else {}) | self.data
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
        total += len(item.model_dump_json().encode()) + 1
        if total > MAX_BYTES:
            if count == 0:
                raise Refusal("too_large", "One record exceeds the 1 MiB message limit")
            return count
    return len(items)


def moment(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def capped(text: str) -> dict:
    """``text`` cut to fit one event, with ``truncated`` when it was cut."""
    if len(text) <= TEXT_CHARS:
        return {"text": text}
    return {"text": text[:TEXT_CHARS], "truncated": True}


def record(message: NativeMessage, session_id: str) -> Record | None:
    """A retained native row as a message record; hidden scaffolding has no record."""
    if message.display_kind == "hidden" or message.role not in {
        "system",
        "user",
        "assistant",
        "tool",
    }:
        return None
    fields = message.model_dump(include={"role", "content", "tool_calls", "tool_call_id"})
    body = {key: value for key, value in fields.items() if key == "content" or value is not None}
    kind = "message"
    if message.display_kind == "failed_turn":
        kind = "failed_turn"
    # The runtime writes its resume prompt itself, so no admission's context joins it; a message
    # the user typed with the same words carries its own admission's context.
    elif (
        message.role == "user"
        and message.client_context is None
        and isinstance(message.content, str)
        and message.content.startswith(CONTINUATION_PREFIX)
    ):
        kind = "continuation"
    return Record(
        id=str(message.id),
        session_id=session_id,
        sequence=message.id,
        message=body,
        kind=kind,
        reasoning=message.reasoning_content or message.reasoning or None,
        created_at=None if message.timestamp is None else moment(message.timestamp),
    )


def request_error(row: Admission) -> RequestError | None:
    """Why a request failed; only ``failed`` and ``interrupted`` requests have an error."""
    if row.status not in {"failed", "interrupted"}:
        return None
    return RequestError(
        error_class=row.error_class,
        code=row.error_class or row.status,
        message=(row.error or row.status)[:ERROR_CHARS],
    )


def chat_request(row: Admission, scope: Scope) -> ChatRequest:
    """A request record from its admission; ``waiting`` and ``error`` follow its status."""
    return ChatRequest(
        request_id=row.op_id,
        session_id=row.session_id,
        scope=scope,
        origin="runtime" if row.input is None else "user",
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revision=row.revision,
        attempt=row.resumes,
        in_history=bool(row.runs),
        error=request_error(row),
        waiting=RequestWaiting(reason=row.error_class, until=row.resume_after)
        if row.status == "waiting"
        else None,
    )


class Progress:
    """One attempt's events, placed from its runs' gateway events in order.

    Each placed event takes the next ``seq``, so seqs are contiguous within the attempt and every
    reader of the same retained events places them alike. Text deltas between boundaries (a tool
    call, interim text) share one completion; ``chunks`` counts its deltas for ``chat.chunk``.
    """

    def __init__(self, attempt: int):
        self.attempt, self.seq = attempt, 0
        self.completion: str | None = None
        self.chunks = 0
        self.consumed: dict[str, int] = {}  # run id -> the last gateway seq placed from it

    def place(self, run_id: str, event: RunEvent) -> ChatEvent | None:
        at = {"attempt": self.attempt, "seq": self.seq + 1, "at": moment(event.timestamp)}
        placed: ChatEvent | None = None
        if event.event == "message.delta" and event.delta:
            if self.completion is None:
                self.completion, self.chunks = f"{run_id}:{self.seq + 1}", 0
            self.chunks += 1
            placed = TextDelta(**at, completion_id=self.completion, delta=event.delta)
        elif event.event == "message.interim" and event.text:
            placed = TextInterim(
                **at, **capped(event.text), already_streamed=event.already_streamed
            )
        elif event.event == "reasoning.available":
            placed = Reasoning(**at, **(capped(event.text) if event.text else {}))
        elif event.event in {"tool.started", "tool.completed"}:
            call = {"call_id": event.tool_call_id or event.tool, "name": event.tool}
            # The gateway streams a display preview of the call, never its JSON arguments;
            # the complete call is in chat.history.
            placed = (
                ToolStarted(
                    **at, **call, arguments=(event.preview or "")[:TEXT_CHARS], truncated=True
                )
                if event.event == "tool.started"
                else ToolCompleted(**at, **call)
            )
        if placed is None:
            return None
        if not isinstance(placed, TextDelta):
            self.completion = None
        self.seq += 1
        return placed


class Subscription:
    def __init__(self, scope: Scope):
        self.scope = scope
        self.frontends: set[UUID] = set()


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
        headers = dict(kwargs.pop("headers", {}))
        propagator.inject(headers)
        response = await client.request(method, path, timeout=30, headers=headers, **kwargs)
        if response.is_success:
            return response.content
        if "op_id was already used for different input" in response.text:
            raise Refusal("id_reused", "This request id was used for different input")
        if response.status_code >= 500:
            raise Refusal("unavailable", response.text, unknown=command)
        if response.status_code == 409:
            # The seam's only scope check answers 409 scope_mismatch.
            code = "forbidden" if "scope_mismatch" in response.text else "conflict"
            raise Refusal(code, response.text)
        code = {400: "invalid", 404: "not_found"}.get(response.status_code, "unsupported")
        raise Refusal(code, response.text)

    async def snapshot(self, **params: str) -> Snapshot:
        """Top-level chat Sessions with their requests, as one consistent seam read."""
        snapshot = Snapshot.model_validate_json(
            await self.call(
                self.seam,
                "GET",
                "/snapshot",
                params={"store": "workspace", "requests": "1", **params},
            )
        )
        if not self.start.done():
            self.start.set_result(snapshot.seq)
        for request in snapshot.records.requests:
            self.requests[request.op_id] = request.session_id
        return snapshot

    async def scoped(self, scope: Scope, **selection: str) -> Snapshot:
        """One session's rows; the seam refuses a scope that differs from the stored one."""
        expected = scope.model_dump(include={"coworld", "league"}, exclude_none=True)
        return await self.snapshot(**selection, **expected)

    async def request(self, session_id: str, scope: Scope, request_id: str) -> Admission:
        snapshot = await self.scoped(scope, session_id=session_id)
        found = [r for r in snapshot.records.requests if r.op_id == request_id]
        if not found:
            raise Refusal("not_found", "No such chat request in this session")
        return found[0]

    async def pending(self, session_id: str, requests: list[Admission]):
        """The prompts waiting on this session's running requests, with their native ids."""
        found = []
        running = sorted(
            {
                r.runs[-1].run_id
                for r in requests
                if r.session_id == session_id and r.status == "running"
            }
        )
        for run_id in running:
            response = await self.gateway.get(f"v1/runs/{quote(run_id, safe='')}", timeout=30)
            if response.status_code == 404:
                continue  # a retired run has no prompts left
            if not response.is_success:
                raise Refusal("unavailable", response.text)
            for control in RunState.model_validate_json(response.content).pending_controls:
                identity = f"aide-input:{session_id}:{run_id}:{control.execution_generation}"
                request_id = str(uuid5(NAMESPACE_URL, f"{identity}:{control.prompt_id}"))
                prompt = control.description or control.command or control.question
                pending = (
                    ApprovalInput(
                        request_id=request_id,
                        prompt=prompt,
                        choices=[c for c in control.choices if c in {"once", "deny"}],
                        description=control.description or None,
                        command=control.command or None,
                    )
                    if control.kind == "approval"
                    else ClarificationInput(request_id=request_id, prompt=prompt)
                )
                found.append((pending, run_id, control))
        return found

    async def messages(self, session_id: str, limit: int, offset: int) -> list[NativeMessage]:
        """Native messages newest first with their admissions' context; empty until the
        session's native opening completes."""
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
        native = NativePage.model_validate_json(page).data
        if not native:
            return []
        annotated = await self.call(
            self.seam, "POST", "/messages/context", json={"session_id": session_id, "data": native}
        )
        return [
            NativeMessage.model_validate(m) for m in NativePage.model_validate_json(annotated).data
        ]

    async def project(self, row: SessionRow, requests: list[Admission]) -> Session:
        """A session from one snapshot, so its requests and active ids always agree."""
        scope = row.scope()
        mine = [r for r in requests if r.session_id == row.id]
        active = [r.op_id for r in mine if r.status in ACTIVE]
        recent = sorted((r for r in mine if r.status in TERMINAL), key=lambda r: r.updated_at)
        embedded = set(active[:EMBEDDED_ACTIVE]) | {r.op_id for r in recent[-RECENT_TERMINAL:]}
        return Session(
            id=row.id,
            parent_session_id=row.parent,
            started_at=row.started,
            ended_at=row.ended,
            active_request_ids=active,
            pending=[pending for pending, _, _ in await self.pending(row.id, mine)],
            scope=scope,
            requests=[chat_request(r, scope) for r in mine if r.op_id in embedded],
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

    # --- progress -----------------------------------------------------------------------------

    async def retained(self, run_id: str) -> list[RunEvent] | None:
        """The gateway's retained events of one run, oldest first, as far as they reach now;
        ``None`` once the gateway no longer holds all of them (retired, or its backlog cut)."""
        found: list[RunEvent] = []
        async with self.gateway.stream(
            "GET",
            f"v1/runs/{quote(run_id, safe='')}/events",
            params={"last_seq": -1},
            timeout=httpx.Timeout(30, read=None),
        ) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            lines = events(response)
            while True:
                try:
                    data = await asyncio.wait_for(anext(lines, None), REPLAY_IDLE_SECONDS)
                except TimeoutError:  # a live run's stream stays open after its retained events
                    return found
                if data is None:
                    return found
                event = RunEvent.model_validate_json(data)
                if event.event == "replay.truncated":
                    return None
                found.append(event)

    async def attempt(self, request: Admission, attempt: int) -> list[ChatEvent] | None:
        """One attempt's placed events, or ``None`` when a run of it is no longer retained."""
        progress, placed = Progress(attempt), []
        for run in (r for r in request.runs if r.attempt == attempt):
            found = await self.retained(run.run_id)
            if found is None:
                return None
            for event in found:
                event = progress.place(run.run_id, event)
                if event is not None:
                    placed.append(event)
        return placed

    async def finish(self, request: Admission, pushed: int = 0) -> Finished:
        """A terminal request's outcome at its durably recorded position: the first time it is
        placed, after its last attempt's retained events and the ``pushed`` events a bridge
        already sent of that attempt (a gateway restart loses the retained ones); the same
        position ever after."""
        placed = request.finished
        if placed is None:
            final = await self.attempt(request, request.resumes)
            body = {
                "op_id": request.op_id,
                "attempt": request.resumes,
                "seq": max(final[-1].seq if final else 0, pushed) + 1,
                "actor": "user:aide",
            }
            reply = await self.call(self.seam, "POST", "/message-finished", json=body)
            placed = Placed.model_validate_json(reply)
        return Finished(
            attempt=placed.attempt,
            seq=placed.seq,
            at=placed.at,
            status=request.status,
            error=request_error(request),
        )

    # --- notifications ----------------------------------------------------------------------

    def touch(self, session_id: str, scope: Scope, frontend_id: UUID) -> None:
        if session_id not in self.subscribers:
            self.subscribers[session_id] = Subscription(scope)
        self.subscribers[session_id].frontends.add(frontend_id)

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
        if isinstance(params, BaseModel):
            params = params.model_dump(mode="json")
        for frontend_id in list(self.subscribers[session_id].frontends):
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            await self.send(self.socket, frontend_id, message)

    async def changed(self, session_id: str) -> None:
        if session_id not in self.subscribers or not self.subscribers[session_id].frontends:
            return
        snapshot = await self.scoped(self.subscribers[session_id].scope, session_id=session_id)
        (row,) = snapshot.records.sessions
        session = await self.project(row, snapshot.records.requests)
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
                    if response.status_code >= 500:  # the seam is restarting; read again
                        log.info("Seam event stream refused (%s)", response.status_code)
                    else:
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
                            elif (
                                event.kind.startswith("client.") and event.subject in self.requests
                            ):
                                await self.changed(self.requests[event.subject])
            except httpx.TransportError:  # the seam stream dropped; resume after the last event
                log.info("Seam event stream dropped; resuming after %s", after)
            await asyncio.sleep(1)

    async def publish(self, request_id: str, session_id: str, after: int) -> int:
        """Announce native messages newer than ``after``; return the newest announced id."""
        fresh: list[NativeMessage] = []
        offset = 0
        while True:
            page = await self.messages(session_id, 100, offset)
            newer = [m for m in page if m.id > after]
            fresh = list(reversed(newer)) + fresh
            if len(newer) < 100:
                break
            offset += 100
        for native in fresh:
            visible = record(native, session_id)
            if visible is not None:
                visible.request_id = request_id
                await self.notify(session_id, "chat.message", visible)
            after = native.id
        return after

    async def follow(
        self, request: Admission, run: Run, progress: Progress, start: tuple[int, int], after: int
    ) -> tuple[bool, int]:
        """Push one run's events placed after ``start`` until its stream ends.

        Returns whether the run is done here (terminal, retired, or no longer placeable) and
        the newest announced message id. ``progress`` carries the attempt's placement across
        reconnections, which resume after the last gateway event it consumed.
        """
        session_id, request_id = request.session_id, request.op_id
        last_seq = progress.consumed.setdefault(run.run_id, -1)
        while True:
            try:
                async with self.gateway.stream(
                    "GET",
                    f"v1/runs/{quote(run.run_id, safe='')}/events",
                    params={"last_seq": last_seq},
                    timeout=httpx.Timeout(30, read=None),
                ) as response:
                    if response.status_code == 404:
                        return True, after  # retired: its messages are in history
                    response.raise_for_status()
                    progressed = False
                    async for data in events(response):
                        event = RunEvent.model_validate_json(data)
                        if event.event == "replay.truncated":
                            return True, after  # unplaceable now; reads report the gap
                        if event.seq is None or event.seq <= last_seq:
                            continue
                        last_seq, progressed = event.seq, True
                        progress.consumed[run.run_id] = last_seq
                        placed = progress.place(run.run_id, event)
                        if placed is not None and (placed.attempt, placed.seq) > start:
                            cursor = {"attempt": placed.attempt, "seq": placed.seq}
                            note = {"session_id": session_id, "request_id": request_id}
                            event_note = note | {"event": placed.model_dump(mode="json")}
                            await self.notify(session_id, "chat.event", event_note)
                            if isinstance(placed, TextDelta):
                                chunk = {
                                    "id": placed.completion_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(event.timestamp),
                                    "model": "",
                                    "choices": [{"index": 0, "delta": {"content": placed.delta}}],
                                }
                                await self.notify(
                                    session_id,
                                    "chat.chunk",
                                    note
                                    | {
                                        "completion_id": placed.completion_id,
                                        "seq": progress.chunks,
                                        "cursor": cursor,
                                        "chunk": chunk,
                                    },
                                )
                        if event.event == "tool.completed":
                            after = await self.publish(request_id, session_id, after)
                        elif event.event in CONTROLS:
                            await self.changed(session_id)
                        elif event.event.removeprefix("run.") in TERMINAL:
                            return True, after
                    if not progressed:
                        return False, after  # a quiet closed stream; the bridge follows again
            except httpx.TransportError:  # the gateway stream dropped; resume after last_seq
                await asyncio.sleep(1)

    async def bridge(self, request_id: str, session_id: str, scope: Scope, fresh: bool) -> None:
        """Push one open request's progress, then its outcome once the seam settles it.

        A fresh bridge (a new chat.send) pushes from the request's beginning; any other starts
        at the progress already placed, so a replayed send or a rebuilt bridge re-sends nothing.
        """
        wake = self.wakes[request_id]
        request = await self.request(session_id, scope, request_id)
        start = (0, 0)
        if not fresh:
            current = await self.attempt(request, request.resumes)
            start = (request.resumes, current[-1].seq if current else 0)
        newest = await self.messages(session_id, 1, 0)
        after = newest[0].id if newest else 0
        done: set[str] = set()
        progress: dict[int, Progress] = {}
        try:
            while True:
                wake.clear()
                request = await self.request(session_id, scope, request_id)
                runs = [r for r in request.runs if r.run_id not in done and r.attempt >= start[0]]
                if runs:
                    run = runs[0]
                    attempt = progress.setdefault(run.attempt, Progress(run.attempt))
                    finished, after = await self.follow(request, run, attempt, start, after)
                    if finished:
                        done.add(run.run_id)
                    elif request.status in ACTIVE:
                        await asyncio.sleep(1)
                    continue
                if request.status in TERMINAL:
                    after = await self.publish(request_id, session_id, after)
                    final = request.resumes
                    pushed = progress[final].seq if final in progress else 0
                    outcome = await self.finish(request, pushed)
                    request = await self.request(session_id, scope, request_id)
                    note = {"session_id": session_id, "request_id": request_id}
                    event_note = note | {"event": outcome.model_dump(mode="json")}
                    await self.notify(session_id, "chat.event", event_note)
                    finished = note | {
                        "status": outcome.status,
                        "cursor": {"attempt": outcome.attempt, "seq": outcome.seq},
                        "revision": request.revision,
                    }
                    if outcome.error is not None:
                        finished["error"] = outcome.error.model_dump(mode="json")
                    await self.notify(session_id, "chat.finished", finished)
                    return
                await wake.wait()
        finally:
            del self.wakes[request_id]

    def ensure_bridge(self, request_id: str, session_id: str, scope: Scope, fresh: bool) -> None:
        if request_id in self.bridges:
            return
        self.requests[request_id] = session_id
        self.wakes[request_id] = asyncio.Event()
        bridge = self.work.create_task(self.bridge(request_id, session_id, scope, fresh))
        self.bridges[request_id] = bridge
        bridge.add_done_callback(lambda _: self.bridges.pop(request_id))

    async def rebuild(self) -> None:
        """Bridge every open request, so pushes resume after a restart without a new send."""
        snapshot = await self.snapshot()
        scopes = {row.id: row.scope() for row in snapshot.records.sessions}
        for request in snapshot.records.requests:
            scope = scopes[request.session_id]
            if request.status in ACTIVE and scope is not None:
                self.ensure_bridge(request.op_id, request.session_id, scope, fresh=False)

    # --- methods ------------------------------------------------------------------------------

    async def open(self, frontend_id: UUID, request_id: str, params: Open) -> dict:
        client = (
            DEFAULT_CLIENT if params.client is None else params.client.model_dump(exclude_none=True)
        )
        body = {
            "op_id": request_id,
            "client": client,
            "actor": "user:aide",
            "session_context": {"contract_version": 1, **params.scope.model_dump()},
        }
        opened = Opened.model_validate_json(
            await self.call(self.seam, "POST", "/session-open", command=True, json=body)
        )
        self.touch(opened.session_id, params.scope, frontend_id)
        snapshot = await self.scoped(params.scope, session_id=opened.session_id)
        (row,) = snapshot.records.sessions
        session = await self.project(row, snapshot.records.requests)
        return {"session": session.model_dump(mode="json")}

    async def send_chat(self, frontend_id: UUID, request_id: str, params: Send) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        if len(params.messages) != 1 or params.messages[0]["role"] != "user":
            raise Refusal("unsupported", "Send exactly one user message")
        snapshot = await self.scoped(params.scope, session_id=params.session_id)
        replay = any(r.op_id == request_id for r in snapshot.records.requests)
        if not replay and not self.inference.available():
            blocking = self.inference.blocking()
            reason = blocking.blocked if blocking else None
            until = blocking.until if blocking else None
            raise Refusal(
                "inference_unavailable",
                "Model work cannot start now",
                reason=reason,
                until=None if until is None else until.isoformat(),
            )
        body = {
            "op_id": request_id,
            "session_id": params.session_id,
            "message": params.messages[0],
            "actor": "user:aide",
        } | params.model_dump(mode="json", include={"context", "instructions"}, exclude_none=True)
        await self.call(self.seam, "POST", "/message", command=True, json=body)
        if not replay:
            self.ensure_bridge(request_id, params.session_id, params.scope, fresh=True)
        return {"accepted": True, "session_id": params.session_id, "request_id": request_id}

    async def read_events(self, frontend_id: UUID, request_id: str, params: Events) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        request = await self.request(params.session_id, params.scope, str(params.request_id))
        after = (params.after.attempt, params.after.seq)
        found: list[ChatEvent] = []
        for attempt in range(params.after.attempt, request.resumes + 1):
            placed = await self.attempt(request, attempt)
            if placed is None and attempt < request.resumes:
                raise Refusal(
                    "history_unavailable",
                    f"Attempt {attempt} is no longer retained; read the request and history",
                )
            # The current attempt's run may be gone (a gateway restart); its outcome still places.
            found += [e for e in placed or [] if (e.attempt, e.seq) > after]
        if request.status in TERMINAL:
            outcome = await self.finish(request)
            found = [e for e in found if (e.attempt, e.seq) < (outcome.attempt, outcome.seq)]
            if (outcome.attempt, outcome.seq) > after:
                found.append(outcome)
        # Events lost with their run (a gateway restart, a cut backlog) leave a gap before the
        # retained ones or the outcome: they are not readable, so the read says so.
        cursors = [after, *((e.attempt, e.seq) for e in found)]
        if any(
            later != (earlier[0], earlier[1] + 1) and (later[0] <= earlier[0] or later[1] != 1)
            for earlier, later in zip(cursors, cursors[1:], strict=False)
        ):
            raise Refusal(
                "history_unavailable",
                "Some of these events are no longer retained; read the request and history",
            )
        page = found[: params.limit]
        page = page[: fitting(page)]
        last = page[-1] if page else None
        return {
            "session_id": params.session_id,
            "request_id": str(params.request_id),
            "events": [event.model_dump(mode="json") for event in page],
            "next": params.after.model_dump()
            if last is None
            else {"attempt": last.attempt, "seq": last.seq},
            "done": request.status in TERMINAL and len(page) == len(found),
        }

    async def history(self, frontend_id: UUID, request_id: str, params: History) -> dict:
        """Backwards from the newest record, or forward after ``after_sequence``.

        A backwards cursor is ``<offset>:<sequence>``: the records older than ``sequence``, whose
        newest sat at native ``offset`` when it was issued. Newer messages only push it further
        down, so the offset is a starting hint and the sequence the boundary.
        """
        self.touch(params.session_id, params.scope, frontend_id)
        await self.scoped(params.scope, session_id=params.session_id)
        if params.cursor is not None and params.after_sequence is not None:
            raise Refusal("invalid", "cursor and after_sequence exclude each other")
        offset, boundary = 0, None
        if params.cursor is not None:
            parts = params.cursor.split(":")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise Refusal("snapshot_expired", "History cursor is not one this backend issued")
            offset, boundary = int(parts[0]), int(parts[1])
        floor = params.after_sequence
        # (record, native offset after it), newest first.
        items: list[tuple[Record, int]] = []
        exhausted = False
        while not exhausted and (floor is not None or len(items) < params.limit):
            page = await self.messages(params.session_id, 100, offset)
            exhausted = len(page) < 100
            for message in page:
                offset += 1
                if floor is not None and message.id <= floor:
                    exhausted = True
                    break
                visible = record(message, params.session_id)
                if visible is not None and (boundary is None or message.id < boundary):
                    items.append((visible, offset))
        if floor is not None:
            forward = [item for item, _ in reversed(items)][: params.limit]
            page = forward[: fitting(forward)]
            return {"items": [item.model_dump(mode="json") for item in page], "next_cursor": None}
        newest = items[: params.limit]
        kept = newest[: fitting([item for item, _ in newest])]
        more = len(kept) < len(items) or not exhausted
        following = f"{kept[-1][1]}:{kept[-1][0].sequence}" if kept and more else None
        return {
            "items": [item.model_dump(mode="json") for item, _ in reversed(kept)],
            "next_cursor": following,
        }

    async def cancel_chat(self, frontend_id: UUID, request_id: str, params: ChatCancel) -> dict:
        self.touch(params.session_id, params.scope, frontend_id)
        target = await self.request(params.session_id, params.scope, str(params.request_id))
        body = {"op_id": str(params.request_id), "actor": "user:aide"}
        await self.call(self.seam, "POST", "/message-cancel", command=True, json=body)
        return {"requested": target.status in ACTIVE}

    async def list_state(self, frontend_id: UUID, request_id: str, params: StateList) -> dict:
        epoch = {"sessions": self.epoch, "requests": "workspace"}.get(params.resource, "coworld")
        if params.resource == "requests":
            if params.session_id is None or params.scope is None:
                raise Refusal("invalid", "Requests are listed per session and scope")
            self.touch(params.session_id, params.scope, frontend_id)
            snapshot = await self.scoped(params.scope, session_id=params.session_id)
            revision = snapshot.seq
            rows = [chat_request(r, params.scope) for r in reversed(snapshot.records.requests)]
        elif params.session_id is not None:
            raise Refusal("invalid", "session_id selects requests only")
        elif params.resource == "sessions":
            snapshot = await self.snapshot()
            revision = self.revision
            rows = [
                row
                for row in snapshot.records.sessions
                if row.scope() is not None and (params.scope is None or row.scope() == params.scope)
            ]
        else:
            coworld = params.scope.coworld if params.scope else params.scope_id
            if coworld is None:
                raise Refusal("unsupported", "Tasks and goals are listed per Coworld")
            revision, rows = await self.board(coworld, params.resource)
        selection = params.model_dump_json(include={"resource", "scope_id", "scope", "session_id"})
        key = hashlib.sha256(selection.encode()).hexdigest()[:16]
        offset = 0
        if params.cursor is not None:
            parts = params.cursor.split(":")
            if len(parts) != 3 or parts[0] != str(revision) or parts[2] != key:
                raise Refusal("snapshot_expired", "The listing changed; read it again")
            offset = int(parts[1])
        page = rows[offset : offset + params.limit]
        if params.resource == "sessions":
            page = [await self.project(row, snapshot.records.requests) for row in page]
        count = fitting(page)
        following = offset + count
        return {
            "resource": params.resource,
            "epoch": epoch,
            "revision": revision,
            "items": [item.model_dump(mode="json") for item in page[:count]],
            "next_cursor": f"{revision}:{following}:{key}" if following < len(rows) else None,
        }

    async def get_state(self, frontend_id: UUID, request_id: str, params: StateGet) -> dict:
        epoch = {"sessions": self.epoch, "requests": "workspace"}.get(params.resource, "coworld")
        if params.resource == "sessions":
            self.touch(params.id, params.scope, frontend_id)
            snapshot = await self.scoped(params.scope, session_id=params.id)
            (row,) = snapshot.records.sessions
            item, revision = await self.project(row, snapshot.records.requests), self.revision
        elif params.resource == "requests":
            snapshot = await self.scoped(params.scope, request_id=params.id)
            ((row,), (session,)) = (
                [r for r in snapshot.records.requests if r.op_id == params.id],
                snapshot.records.sessions,
            )
            self.touch(session.id, params.scope, frontend_id)
            item = chat_request(row, params.scope)
            if row.input is not None and not item.in_history:
                item.messages = [row.input]
                if len(item.model_dump_json().encode()) > MAX_BYTES - 1024:
                    item.messages, item.message_omitted = None, True
            revision = row.revision
        else:
            if params.scope.coworld is None:
                raise Refusal("unsupported", "Tasks and goals belong to a Coworld")
            revision, items = await self.board(params.scope.coworld, params.resource)
            found = [item for item in items if item.id == params.id]
            if not found:
                raise Refusal("not_found", f"No such {params.resource[:-1]}")
            (item,) = found
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
        snapshot = await self.scoped(params.scope, session_id=params.session_id)
        found = [
            (run_id, control)
            for pending, run_id, control in await self.pending(
                params.session_id, snapshot.records.requests
            )
            if pending.request_id == str(params.request_id) and pending.kind == params.response.kind
        ]
        if not found:
            raise Refusal("conflict", "That input is no longer pending")
        ((run_id, control),) = found
        answer = params.response.model_dump(exclude={"kind"})
        action = "approval" if control.kind == "approval" else "clarify"
        headers = {"Idempotency-Key": request_id}
        propagator.inject(headers)
        response = await self.gateway.post(
            f"v1/runs/{quote(run_id, safe='')}/{action}",
            json={
                "request_id": control.prompt_id,
                "execution_generation": control.execution_generation,
                **answer,
            },
            headers=headers,
            timeout=30,
        )
        if response.status_code == 400:
            raise Refusal("invalid", response.text)
        if response.status_code in {404, 409} or (
            response.is_success
            and Resolution.model_validate_json(response.content).status != "resolved"
        ):
            raise Refusal("conflict", "That input is no longer pending")
        if not response.is_success:
            raise Refusal("unavailable", response.text, unknown=True)
        return {"applied": True}

    # --- the connection -----------------------------------------------------------------------

    async def answer(self, socket: ClientConnection, frame: Frame) -> None:
        with span("workspace.aide.request", context=propagator.extract(frame.trace_context)):
            message = frame.message
            request_id, method = str(message["id"]), str(message["method"])
            handlers = {
                "session.open": (Open, self.open),
                "chat.send": (Send, self.send_chat),
                "chat.history": (History, self.history),
                "chat.events": (Events, self.read_events),
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

    def closed(self, frontend_id: UUID) -> None:
        """The relay reports a frontend connection ended: its subscriptions end with it."""
        for subscription in self.subscribers.values():
            subscription.frontends.discard(frontend_id)

    async def connect(self, url: str, credential: str) -> None:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {credential}"},
            subprotocols=["aide.1.0.0"],
            max_size=2 * MAX_BYTES,
        ) as socket:
            Bound.model_validate_json(await socket.recv())
            self.socket = socket
            await self.rebuild()
            try:
                async with asyncio.TaskGroup() as replies:
                    try:
                        async for raw in socket:
                            frame = Frame.model_validate_json(raw)
                            # This backend sends no reverse requests, so no replies arrive.
                            if frame.message.get("method") == "frontend.closed":
                                self.closed(frame.frontend_id)
                            elif "method" in frame.message and "id" in frame.message:
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
