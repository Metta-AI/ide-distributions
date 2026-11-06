"""The image's AIDE 1.0.0 backend over a real seam and a scripted gateway."""

import asyncio
import json
import sys
import threading
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest

from research_core import run_failures
from research_core.serve import SeamServer
from research_core.store import admissions, goals, sessions, settings, tasks
from research_core.watcher.hermes_api import FakeHermesRuns
from tests.test_serve import COWORLD_ID, HARNESS, make_ctx

IMAGE = Path(__file__).resolve().parents[2] / "image"


class Availability:
    """The image's inference gate; its real module needs the Hermes interpreter's dotenv."""

    def __init__(self):
        self.open = True

    def available(self):
        return self.open

    def blocking(self):
        return None


sys.modules.setdefault("inference", types.SimpleNamespace(Availability=Availability))
sys.path.insert(0, str(IMAGE))
import aide  # noqa: E402

WORKSPACE = {"scope": "workspace", "coworld": None, "league": None}
LEAGUE = {"scope": "coworld", "coworld": COWORLD_ID, "league": "league_1"}
FRONTEND, OTHER = uuid4(), uuid4()


class Socket:
    def __init__(self):
        self.frames: list[dict] = []

    async def send(self, text: str) -> None:
        frame = json.loads(text)
        assert len(json.dumps(frame["message"], separators=(",", ":")).encode()) <= aide.MAX_BYTES
        self.frames.append(frame)

    def notes(self, method: str, frontend=FRONTEND) -> list[dict]:
        return [
            f["message"]["params"]
            for f in self.frames
            if f["frontend_id"] == str(frontend) and f["message"].get("method") == method
        ]


class Gateway:
    """The gateway routes the backend reads: transcripts, run state and run event streams.

    Each run's events come from ``script`` once. Like Hermes's per-run buffer, the gateway
    retains only the newest ``capacity`` events a run has reached, in memory: a read from
    before them starts with ``replay.truncated``, and ``restart`` loses every run. Their ``say``
    messages join the transcript when the run first reaches them. A ``gate`` entry holds the
    run open until it is set.
    """

    def __init__(self, hermes: FakeHermesRuns):
        self.hermes = hermes
        self.transcripts: dict[str, list[dict]] = {}
        self.controls: dict[str, list[dict]] = {}
        self.event_requests: list[tuple[str, str]] = []
        self.drop_after: int | None = None
        self.answers: list[tuple[str, dict]] = []
        self.runs: dict[str, list[dict]] = {}
        self.retired: set[str] = set()
        self.gate = asyncio.Event()
        self.capacity = 1000
        self.reached: dict[str, int] = {}  # run id -> the newest seq the run has produced

    def restart(self) -> None:
        """The gateway process restarts: its in-memory runs and their buffers are gone."""
        self.retired |= set(self.runs)

    def say(self, session_id: str, **message) -> None:
        rows = self.transcripts.setdefault(session_id, [])
        number = 100 + sum(len(t) for t in self.transcripts.values())
        rows.append({"id": number, "timestamp": 1793980800.0 + number, **message})

    def script(self, run_id: str) -> list[dict]:
        call = {"id": "call_1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
        done = {"role": "assistant", "content": "Done", "reasoning": "Listing first."}
        return [
            {"event": "run.started"},
            {"event": "reasoning.available", "text": "Listing first."},
            {"event": "message.delta", "delta": "Hel"},
            {"event": "message.delta", "delta": "lo"},
            {
                "event": "tool.started",
                "tool": "ls",
                "preview": "ls",
                "tool_call_id": "call_1",
                "say": [{"role": "assistant", "content": None, "tool_calls": [call]}],
            },
            {
                "event": "tool.completed",
                "tool": "ls",
                "tool_call_id": "call_1",
                "say": [{"role": "tool", "content": "files", "tool_call_id": "call_1"}],
            },
            {"event": "message.delta", "delta": "Done"},
            {"event": "run.completed", "say": [done]},
        ]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/p/orchestrator/")
        if path.startswith("api/sessions/"):
            session_id = path.split("/")[2]
            if session_id not in self.hermes.sessions:
                return httpx.Response(404, json={"error": "Session not found"})
            # Hermes ``order=latest``: offset and limit count back from the newest, and the page
            # comes back oldest first.
            assert request.url.params["order"] == "latest"
            rows = list(reversed(self.transcripts.get(session_id, [])))
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(200, json={"data": rows[offset : offset + limit][::-1]})
        run_id = path.split("/")[2]
        if run_id in self.retired:
            return httpx.Response(404, json={"error": "Run not found"})
        if path.endswith("/events"):
            last_seq = int(request.url.params["last_seq"])
            self.event_requests.append((run_id, request.url.params["last_seq"]))
            return httpx.Response(200, content=self.stream(run_id, last_seq))
        if path.endswith(("/approval", "/clarify")):
            self.answers.append((path.rsplit("/", 1)[1], json.loads(request.content)))
            self.controls[run_id] = []
            return httpx.Response(200, json={"run_id": run_id, "status": "resolved"})
        return httpx.Response(200, json={"pending_controls": self.controls.get(run_id, [])})

    async def stream(self, run_id: str, last_seq: int):
        if run_id not in self.runs:
            self.runs[run_id] = self.script(run_id)
        yield b": open\n\n"
        oldest = self.reached.get(run_id, -1) - self.capacity + 1
        if last_seq + 1 < oldest:
            yield f"data: {json.dumps({'event': 'replay.truncated'})}\n\n".encode()
        for seq, event in enumerate(self.runs[run_id]):
            if seq <= max(last_seq, oldest - 1):
                continue
            self.reached[run_id] = max(self.reached.get(run_id, -1), seq)
            if event.get("gate"):
                await self.gate.wait()
                continue
            if self.drop_after is not None and seq > self.drop_after:
                self.drop_after = None
                raise httpx.ReadError("gateway stream dropped")
            for message in event.pop("say", []):
                self.say(self.hermes.runs[run_id].session_id, **message)
            if event["event"] == "run.completed" and self.hermes.runs[run_id].status == "running":
                self.hermes.complete(run_id, "Done")
            payload = {**event, "run_id": run_id, "seq": seq, "timestamp": 1793980800.0 + seq}
            yield f"id: {seq}\ndata: {json.dumps(payload)}\n\n".encode()
            await asyncio.sleep(0)
        yield b": stream closed\n\n"


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def __call__(self):
        return self.now


@pytest.fixture
def system(tmp_path):
    ctx = make_ctx(tmp_path, coworld=True)
    hermes = FakeHermesRuns()
    clock = Clock()
    server = SeamServer(ws=ctx.workspace, hermes=hermes, env=dict(ctx.env), clock=clock)
    server.test_clock = clock
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield ctx, hermes, server
    server.shutdown()
    worker.join(timeout=10)


def run(system, scenario, inference=None, seam_transport=None, gateway=None):
    ctx, hermes, server = system
    gateway = gateway or Gateway(hermes)

    async def main():
        async with (
            httpx.AsyncClient(base_url=server.url, transport=seam_transport) as seam,
            httpx.AsyncClient(
                base_url="http://gateway/p/orchestrator/",
                transport=httpx.MockTransport(gateway.handle),
            ) as native,
            asyncio.TaskGroup() as work,
        ):
            backends, watchers = [], []

            def tasks():
                return watchers + [task for old in backends for task in old.consumers.values()]

            def stop():
                for task in tasks():
                    task.cancel()

            def restart():
                """A new backend process: nothing of the last one's memory survives."""
                stop()
                backend = aide.Backend(seam, native, inference or Availability(), work)
                backend.socket = Socket()
                backend.restart = restart
                backends.append(backend)
                watchers.append(work.create_task(backend.watch()))
                return backend

            try:
                async with asyncio.timeout(30):
                    await scenario(restart(), gateway, ctx, hermes)
            finally:
                # A cancel can be lost while a task reads the seam stream (cause not yet
                # known), and the task group would then wait forever. Repeat it until it lands.
                while not all(task.done() for task in tasks()):
                    stop()
                    await asyncio.sleep(0.05)

    asyncio.run(main())


async def call(backend, method, params, *, request_id=None, frontend=FRONTEND):
    request_id = request_id or str(uuid4())
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    await backend.answer(backend.socket, aide.Frame(frontend_id=frontend, message=message))
    replies = [f["message"] for f in backend.socket.frames if f["message"].get("id") == request_id]
    return replies[-1]


async def until(check):
    while not check():
        await asyncio.sleep(0.02)


async def opened(backend, scope=WORKSPACE):
    reply = await call(backend, "session.open", {"scope": scope})
    return reply["result"]["session"]["id"]


def say(text):
    return [{"role": "user", "content": text}]


def code(reply):
    return reply["error"]["data"]["code"]


def test_session_open_replays_its_session_and_refuses_reused_ids(system):
    async def scenario(backend, gateway, ctx, hermes):
        request_id = str(uuid4())
        first = await call(backend, "session.open", {"scope": LEAGUE}, request_id=request_id)
        session = first["result"]["session"]
        assert session["scope"] == LEAGUE and session["pending"] == []
        again = await call(backend, "session.open", {"scope": LEAGUE}, request_id=request_id)
        assert again["result"]["session"]["id"] == session["id"]
        reused = await call(backend, "session.open", {"scope": WORKSPACE}, request_id=request_id)
        assert code(reused) == "id_reused"
        missing = await call(
            backend, "state.get", {"resource": "sessions", "id": "nope", **{"scope": WORKSPACE}}
        )
        assert code(missing) == "not_found"

    run(system, scenario)
    ctx = system[0]
    assert ctx.wstore.one("SELECT COUNT(*) FROM client_admissions WHERE kind='open'")[0] == 1


def events_read(backend, session_id, request_id, after=None, **extra):
    params = {
        "session_id": session_id,
        "request_id": request_id,
        "after": after or {"attempt": 0, "seq": 0},
        "scope": WORKSPACE,
    }
    return call(backend, "chat.events", params | extra)


def cursor(event):
    return (event["attempt"], event["seq"])


def test_chat_round_trip_pushes_what_the_reads_return_to_every_subscriber(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        watching = await call(
            backend,
            "state.get",
            {"resource": "sessions", "id": session_id, "scope": WORKSPACE},
            frontend=OTHER,
        )
        assert watching["result"]["item"]["id"] == session_id
        request_id = str(uuid4())
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        accepted = await call(backend, "chat.send", params, request_id=request_id)
        assert accepted["result"] == {
            "accepted": True,
            "session_id": session_id,
            "request_id": request_id,
        }
        socket = backend.socket
        await until(lambda: socket.notes("chat.finished") and socket.notes("chat.finished", OTHER))
        (run_id,) = hermes.runs
        pushed = [n["event"] for n in socket.notes("chat.event")]
        assert [e["kind"] for e in pushed] == [
            "reasoning",
            "text.delta",
            "tool.started",
            "tool.completed",
            "text.delta",
            "finished",
        ]
        # Consecutive deltas merge into one event; every event takes the next seq of its attempt.
        assert [e.get("delta") for e in pushed if e["kind"] == "text.delta"] == ["Hello", "Done"]
        assert [cursor(e) for e in pushed] == [(0, n) for n in range(1, 7)]
        assert pushed[2] | {"at": None} == {
            "attempt": 0,
            "seq": 3,
            "at": None,
            "kind": "tool.started",
            "call_id": "call_1",
            "name": "ls",
            "arguments": "ls",
            "truncated": True,
        }
        for frontend in (FRONTEND, OTHER):
            chunks = socket.notes("chat.chunk", frontend)
            assert [(c["completion_id"], c["seq"]) for c in chunks] == [
                (f"{run_id}:2", 1),
                (f"{run_id}:5", 1),
            ]
            # A chunk mirrors the text.delta at its cursor.
            assert [
                (c["cursor"]["seq"], c["chunk"]["choices"][0]["delta"]["content"]) for c in chunks
            ] == [(e["seq"], e["delta"]) for e in pushed if e["kind"] == "text.delta"]
            messages = socket.notes("chat.message", frontend)
            assert [m["message"]["role"] for m in messages] == ["assistant", "tool", "assistant"]
            assert all(m["request_id"] == request_id and m["kind"] == "message" for m in messages)
            assert messages[-1]["reasoning"] == "Listing first." and messages[-1]["created_at"]
            (finished,) = socket.notes("chat.finished", frontend)
            assert finished["status"] == "completed"
            assert finished["cursor"] == {"attempt": 0, "seq": 6}
            changes = socket.notes("state.changed", frontend)
            assert any(request_id in c["item"]["active_request_ids"] for c in changes)
            assert all(c["previous_revision"] + 1 == c["revision"] for c in changes)
        read = (await events_read(backend, session_id, request_id))["result"]
        assert read["events"] == pushed and read["done"] is True
        assert read["next"] == {"attempt": 0, "seq": 6}
        assert (await events_read(backend, session_id, request_id))["result"] == read
        tail = await events_read(backend, session_id, request_id, {"attempt": 0, "seq": 5})
        assert tail["result"]["events"] == pushed[5:]
        paged = (await events_read(backend, session_id, request_id, limit=2))["result"]
        assert paged["events"] == pushed[:2] and paged["done"] is False
        assert paged["next"] == {"attempt": 0, "seq": 2}
        past = (await events_read(backend, session_id, request_id, read["next"]))["result"]
        assert past == read | {"events": [], "next": read["next"], "done": True}
        record = (
            await call(
                backend, "state.get", {"resource": "requests", "id": request_id, "scope": WORKSPACE}
            )
        )["result"]
        assert record["item"]["revision"] == finished["revision"] == record["revision"]
        assert record["item"]["status"] == "completed" and record["item"]["in_history"] is True
        assert "messages" not in record["item"]
        history = await call(
            backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE}
        )
        items = history["result"]["items"]
        assert [i["message"]["role"] for i in items] == ["assistant", "tool", "assistant"]
        assert [i["sequence"] for i in items] == sorted(i["sequence"] for i in items)
        assert history["result"]["next_cursor"] is None
        # A retry of the accepted request returns the original acceptance and pushes nothing.
        frames = len(socket.frames)
        again = await call(backend, "chat.send", params, request_id=request_id)
        assert again["result"] == accepted["result"]
        assert all("id" in f["message"] for f in socket.frames[frames:])
        changed = {**params, "messages": say("other")}
        assert code(await call(backend, "chat.send", changed, request_id=request_id)) == "id_reused"

        # A new backend process reads the same records, events and seqs from durable state.
        requested = len(gateway.event_requests)
        restarted = backend.restart()
        assert (await events_read(restarted, session_id, request_id))["result"] == read
        reread = await call(
            restarted, "state.get", {"resource": "requests", "id": request_id, "scope": WORKSPACE}
        )
        assert reread["result"] == record
        session = (
            await call(
                restarted,
                "state.get",
                {"resource": "sessions", "id": session_id, "scope": WORKSPACE},
            )
        )["result"]["item"]
        assert [r["request_id"] for r in session["requests"]] == [request_id]
        assert session["active_request_ids"] == []
        # The log, not the gateway, answers: a gateway restart changes no read.
        gateway.restart()
        assert (await events_read(restarted, session_id, request_id))["result"] == read
        assert all(run != run_id for run, _ in gateway.event_requests[requested:])

    run(system, scenario)
    ctx, hermes, _ = system
    assert [name for name, _ in hermes.calls].count("start") == 1


def test_a_dropped_run_stream_resumes_from_its_last_sequence_without_duplicates(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        gateway.drop_after = 2
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params)
        await until(lambda: backend.socket.notes("chat.finished"))
        # The consumer resumes after the drop, and the delta merge spans it.
        assert [last for _, last in gateway.event_requests] == ["-1", "2"]
        deltas = [
            c["chunk"]["choices"][0]["delta"]["content"] for c in backend.socket.notes("chat.chunk")
        ]
        assert deltas == ["Hello", "Done"]
        seqs = [n["event"]["seq"] for n in backend.socket.notes("chat.event")]
        assert seqs == list(range(1, 7))

    run(system, scenario)


class SeamFault(httpx.AsyncBaseTransport):
    """The real seam, except that the ``failing``-th matching request gets no answer: it times out
    before reaching the route, or after the route has committed and answered."""

    def __init__(self, method, path, failing, *, committed):
        self.real = httpx.AsyncHTTPTransport()
        self.method, self.path, self.failing, self.committed = method, path, failing, committed
        self.matched = 0
        self.answers: list[int] = []  # the status of each matching request after the fault

    async def handle_async_request(self, request):
        if (request.method, request.url.path) != (self.method, self.path):
            return await self.real.handle_async_request(request)
        self.matched += 1
        if self.matched == self.failing and not self.committed:
            raise httpx.ReadTimeout("timed out before the route")
        response = await self.real.handle_async_request(request)
        if self.matched == self.failing:
            await response.aclose()
            raise httpx.ReadTimeout("timed out after the route committed")
        if self.matched > self.failing:
            self.answers.append(response.status_code)
        return response


@pytest.mark.parametrize("committed", [False, True], ids=["before-commit", "after-commit"])
@pytest.mark.parametrize(
    "failing",
    # The second append is the merged text of a batch, with the tool call still to be sent.
    [1, 2],
    ids=["one-event", "first-of-two-events"],
)
def test_an_append_with_no_answer_ends_the_backend_and_the_next_one_resumes_the_log(
    system, failing, committed
):
    _, hermes, _ = system
    gateway = Gateway(hermes)
    fault = SeamFault("POST", "/message-events", failing, committed=committed)
    request_id = str(uuid4())

    async def send(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params, request_id=request_id)
        await until(lambda: backend.socket.notes("chat.finished"))

    with pytest.raises(ExceptionGroup) as ended:
        run(system, send, seam_transport=fault, gateway=gateway)
    (refusal,) = ended.value.exceptions
    assert isinstance(refusal, aide.Refusal) and refusal.code == "unavailable"
    assert isinstance(refusal.__cause__, httpx.ReadTimeout)
    # It stopped there: no later event reached the log, and the stream was not followed again.
    assert fault.answers == [] and len(gateway.event_requests) == 1

    async def resume(backend, gateway, ctx, hermes):
        (session_id,) = hermes.sessions
        # The reloaded frontend subscribes before the new backend's first await.
        state = {"resource": "sessions", "id": session_id, "scope": WORKSPACE}
        await call(backend, "state.get", state)
        await until(lambda: backend.socket.notes("chat.finished"))
        read = (await events_read(backend, session_id, request_id))["result"]
        assert [cursor(e) for e in read["events"]] == [(0, n) for n in range(1, 7)]
        assert [e["kind"] for e in read["events"]] == [
            "reasoning",
            "text.delta",
            "tool.started",
            "tool.completed",
            "text.delta",
            "finished",
        ]
        assert read["done"] is True
        # The next backend pushes only what the log did not hold, so a push answered by a timeout
        # stays lost; the frontend reads it with chat.events.
        logged_before = failing if committed else failing - 1
        pushed = [n["event"]["seq"] for n in backend.socket.notes("chat.event")]
        assert pushed == list(range(logged_before + 1, 7))

    run(system, resume, gateway=gateway)
    assert [name for name, _ in hermes.calls].count("start") == 1


def test_a_failed_read_for_a_push_follows_the_stream_again_and_loses_nothing(system):
    """Unlike an append, a read leaves the log's position alone, so the consumer keeps going."""
    fault = SeamFault("GET", "/session-pending", 2, committed=False)

    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params)
        await until(lambda: backend.socket.notes("chat.finished"))
        assert len(gateway.event_requests) == 2
        seqs = [n["event"]["seq"] for n in backend.socket.notes("chat.event")]
        assert seqs == list(range(1, 7))
        messages = backend.socket.notes("chat.message")
        assert [m["message"]["role"] for m in messages] == ["assistant", "tool", "assistant"]

    run(system, scenario, seam_transport=fault)


def test_a_rebuilt_backend_resumes_pushes_for_an_open_request_without_a_new_send(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        gateway.script = lambda run_id: [
            {"event": "run.started"},
            {"event": "message.delta", "delta": "Hel"},
            {"gate": True},
            {"event": "message.delta", "delta": "lo"},
            {"event": "run.completed"},
        ]
        request_id = str(uuid4())
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params, request_id=request_id)
        await until(lambda: backend.socket.notes("chat.chunk"))
        # The live run's stream stays open: a read returns what is retained so far.
        live = (await events_read(backend, session_id, request_id))["result"]
        assert [(e["seq"], e["delta"]) for e in live["events"]] == [(1, "Hel")]
        assert live["done"] is False

        restarted = backend.restart()
        await until(lambda: list(restarted.consumers) == [request_id])
        # A replayed send attaches to the open request and starts nothing.
        await call(restarted, "chat.send", params, request_id=request_id)
        await asyncio.sleep(0.5)
        gateway.gate.set()
        await until(lambda: restarted.socket.notes("chat.finished"))
        chunks = restarted.socket.notes("chat.chunk")
        assert [(c["cursor"], c["chunk"]["choices"][0]["delta"]["content"]) for c in chunks] == [
            ({"attempt": 0, "seq": 2}, "lo")
        ]
        # The resumed completion keeps its id and counts on from the chunk logged before.
        (run_id,) = hermes.runs
        assert [(c["completion_id"], c["seq"]) for c in chunks] == [(f"{run_id}:1", 2)]
        assert [n["event"]["seq"] for n in restarted.socket.notes("chat.event")] == [2, 3]
        assert restarted.socket.notes("chat.finished")[0]["cursor"] == {"attempt": 0, "seq": 3}

    run(system, scenario)
    assert [name for name, _ in system[1].calls].count("start") == 1


def test_a_held_request_reads_waiting_then_stitches_its_next_attempt(system):
    ctx, hermes, server = system

    def fail_first(run):
        if len(hermes.runs) == 1:
            hermes.fail(run.run_id, "HTTP 429: slow down")

    async def scenario(backend, gateway, ctx, hermes):
        hermes.script = fail_first
        session_id = await opened(backend)

        def script(run_id):
            if run_id == next(iter(hermes.runs)):
                return [
                    {"event": "run.started"},
                    {"event": "message.delta", "delta": "Wor"},
                    {"event": "run.failed", "error": "HTTP 429: slow down"},
                ]
            return [
                {"event": "run.started"},
                {"event": "message.delta", "delta": "Back"},
                {"event": "run.completed"},
            ]

        gateway.script = script
        request_id = str(uuid4())
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params, request_id=request_id)
        get = {"resource": "requests", "id": request_id, "scope": WORKSPACE}

        async def status():
            return (await call(backend, "state.get", get))["result"]["item"]

        while (await status())["status"] != "waiting":
            await asyncio.sleep(0.05)
        # A new backend reads the hold, its reason and its resume time from the store.
        restarted = backend.restart()
        held = (await call(restarted, "state.get", get))["result"]["item"]
        assert held["waiting"]["reason"] == "rate_limited" and held["waiting"]["until"]
        assert (held["error"], held["attempt"], held["in_history"]) == (None, 0, True)
        server.test_clock.now += timedelta(minutes=2)
        await until(lambda: restarted.socket.notes("chat.finished"))
        read = (await events_read(restarted, session_id, request_id))["result"]
        assert [(cursor(e), e["kind"]) for e in read["events"]] == [
            ((0, 1), "text.delta"),
            ((1, 1), "text.delta"),
            ((1, 2), "finished"),
        ]
        (chunk,) = restarted.socket.notes("chat.chunk")
        assert chunk["cursor"] == {"attempt": 1, "seq": 1}
        assert (await call(restarted, "state.get", get))["result"]["item"]["attempt"] == 1
        # The first attempt reads from the log after the gateway restarts.
        gateway.restart()
        assert (await events_read(restarted, session_id, request_id))["result"] == read
        current = await events_read(restarted, session_id, request_id, {"attempt": 1, "seq": 0})
        assert current["result"]["events"] == read["events"][1:]

    run(system, scenario)


def test_failed_and_unstarted_requests_stay_readable_after_a_restart(system):
    async def scenario(backend, gateway, ctx, hermes):
        hermes.script = lambda run: hermes.fail(run.run_id, "agent run failed")
        gateway.script = lambda run_id: [{"event": "run.started"}, {"event": "run.failed"}]
        session_id = await opened(backend)
        failed_id = str(uuid4())
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params, request_id=failed_id)
        await until(lambda: backend.socket.notes("chat.finished"))
        settings.update(ctx.wstore, HARNESS, "paused", True, cites="hold dispatch")
        queued_id = str(uuid4())
        queued = {"session_id": session_id, "messages": say("later"), "scope": WORKSPACE}
        await call(backend, "chat.send", queued, request_id=queued_id)
        cancel = {"session_id": session_id, "request_id": queued_id, "scope": WORKSPACE}
        assert (await call(backend, "chat.cancel", cancel))["result"] == {"requested": True}
        await until(lambda: len(backend.socket.notes("chat.finished")) == 2)

        restarted = backend.restart()
        listing = (
            await call(
                restarted,
                "state.list",
                {"resource": "requests", "session_id": session_id, "scope": WORKSPACE},
            )
        )["result"]
        assert [(i["request_id"], i["status"]) for i in listing["items"]] == [
            (queued_id, "cancelled"),
            (failed_id, "failed"),
        ]
        failed, cancelled = listing["items"][1], listing["items"][0]
        assert failed["error"] == {"class": None, "code": "failed", "message": "agent run failed"}
        assert (cancelled["error"], cancelled["in_history"]) == (None, False)
        assert "messages" not in cancelled
        got = await call(
            restarted, "state.get", {"resource": "requests", "id": queued_id, "scope": WORKSPACE}
        )
        assert got["result"]["item"]["messages"] == say("later")
        outcome = (await events_read(restarted, session_id, failed_id))["result"]["events"]
        assert [(e["kind"], e["status"], e["error"]["code"]) for e in outcome] == [
            ("finished", "failed", "failed")
        ]
        never = (await events_read(restarted, session_id, queued_id))["result"]
        assert [(cursor(e), e["status"]) for e in never["events"]] == [((0, 1), "cancelled")]
        assert (
            code(
                await call(
                    restarted,
                    "state.get",
                    {"resource": "requests", "id": queued_id, "scope": LEAGUE},
                )
            )
            == "forbidden"
        )

    run(system, scenario)


def midway(run_id):
    return [
        {"event": "run.started"},
        {"event": "reasoning.available", "text": "Thinking."},
        {"event": "message.delta", "delta": "Hel"},
        {"event": "message.delta", "delta": "lo"},
        {"event": "tool.started", "tool": "ls", "preview": "ls", "tool_call_id": "call_1"},
        {"gate": True},
        {"event": "tool.completed", "tool": "ls", "tool_call_id": "call_1"},
        {"event": "message.delta", "delta": "Done"},
        {"event": "run.completed"},
    ]


def started(backend):
    return any(n["event"]["kind"] == "tool.started" for n in backend.socket.notes("chat.event"))


def test_a_backend_restart_mid_run_keeps_every_logged_event_and_finishes_the_run(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        # Hermes keeps only a few events per run, so the start of the run is gone from it.
        gateway.capacity, gateway.script = 3, midway
        request_id = str(uuid4())
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params, request_id=request_id)
        await until(lambda: started(backend))
        before = [n["event"] for n in backend.socket.notes("chat.event")]

        restarted = backend.restart()
        live = (await events_read(restarted, session_id, request_id))["result"]
        assert live["events"] == before and live["done"] is False
        assert [e["kind"] for e in before] == ["reasoning", "text.delta", "tool.started"]
        gateway.gate.set()
        await until(lambda: restarted.socket.notes("chat.finished"))
        after = [n["event"] for n in restarted.socket.notes("chat.event")]
        read = (await events_read(restarted, session_id, request_id))["result"]
        assert read["events"] == before + after and read["done"] is True
        assert [cursor(e) for e in read["events"]] == [(0, n) for n in range(1, 7)]
        assert [e["kind"] for e in after] == ["tool.completed", "text.delta", "finished"]
        assert after[-1]["status"] == "completed"

    run(system, scenario)
    assert [name for name, _ in system[1].calls].count("start") == 1


def test_a_run_gone_after_a_full_restart_ends_interrupted_with_its_events_readable(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        gateway.script = midway
        request_id = str(uuid4())
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params, request_id=request_id)
        await until(lambda: started(backend))
        before = [n["event"] for n in backend.socket.notes("chat.event")]
        # The whole runtime restarts: Hermes loses the run and its buffer with it.
        (run_id,) = hermes.runs
        del hermes.runs[run_id]
        gateway.restart()
        restarted = backend.restart()
        # The reloaded frontend subscribes before the new backend's first await.
        state = {"resource": "sessions", "id": session_id, "scope": WORKSPACE}
        await call(restarted, "state.get", state)
        await until(lambda: restarted.socket.notes("chat.finished"))
        (finished,) = restarted.socket.notes("chat.finished")
        assert finished["status"] == "interrupted" and finished["error"]["code"] == "interrupted"
        assert finished["cursor"] == {"attempt": 0, "seq": 4}
        read = (await events_read(restarted, session_id, request_id))["result"]
        assert read["events"][:3] == before and read["done"] is True
        (outcome,) = [n["event"] for n in restarted.socket.notes("chat.event")]
        assert read["events"][3] == outcome
        assert (outcome["kind"], outcome["status"]) == ("finished", "interrupted")
        assert outcome["error"]["message"]
        # A frontend that applied the pushed events reads the outcome right after them.
        tail = await events_read(restarted, session_id, request_id, {"attempt": 0, "seq": 3})
        assert tail["result"]["events"] == [outcome]
        assert tail["result"]["next"] == finished["cursor"]

    run(system, scenario)


def test_only_a_chat_cancel_ends_a_running_request_cancelled(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        gateway.script = lambda run_id: [
            {"event": "run.started"},
            {"gate": True},
            {"event": "run.cancelled"},
        ]
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        outcomes = {}
        for how in ("workspace stop", "chat.cancel"):
            request_id = str(uuid4())
            await call(backend, "chat.send", params, request_id=request_id)
            await until(lambda: len(hermes.runs) == len(outcomes) + 1)
            run_id = list(hermes.runs)[-1]
            if how == "chat.cancel":
                cancel = {"session_id": session_id, "request_id": request_id, "scope": WORKSPACE}
                assert (await call(backend, "chat.cancel", cancel))["result"] == {"requested": True}
            else:
                # A drain or shutdown stops the run natively; nobody cancelled the request.
                hermes.stop(run_id, profile="orchestrator")
            gateway.gate.set()
            await until(lambda: len(backend.socket.notes("chat.finished")) == len(outcomes) + 1)
            gateway.gate = asyncio.Event()
            got = await call(
                backend, "state.get", {"resource": "requests", "id": request_id, "scope": WORKSPACE}
            )
            outcomes[how] = got["result"]["item"]
        stopped, cancelled = outcomes["workspace stop"], outcomes["chat.cancel"]
        assert stopped["status"] == "interrupted"
        assert stopped["error"] == {
            "class": None,
            "code": "interrupted",
            "message": "The workspace stopped the run",
        }
        assert (cancelled["status"], cancelled["error"]) == ("cancelled", None)
        finished = backend.socket.notes("chat.finished")
        assert [(f["status"], f.get("error")) for f in finished] == [
            ("interrupted", stopped["error"]),
            ("cancelled", None),
        ]

    run(system, scenario)


def test_chat_send_forwards_optional_context_and_instructions_to_the_admission(system):
    view = {"id": "league-standings", "label": "League standings"}
    sent = {}

    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        plain = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        sent["plain"] = (await call(backend, "chat.send", plain))["result"]["request_id"]
        await until(lambda: backend.socket.notes("chat.finished"))
        rich = {
            **plain,
            "context": {"contract_version": 1, "view": view},
            "instructions": "Answer briefly.",
        }
        sent["rich"] = (await call(backend, "chat.send", rich))["result"]["request_id"]

    run(system, scenario)
    ctx = system[0]

    def payload(op_id):
        row = ctx.wstore.one("SELECT payload FROM client_admissions WHERE op_id=?", (op_id,))
        return json.loads(row["payload"])

    plain, rich = payload(sent["plain"]), payload(sent["rich"])
    assert "view" not in plain["context"] and plain["instructions"] is None
    assert rich["context"]["view"] == view and rich["instructions"] == "Answer briefly."


def test_session_open_stamps_the_named_client_and_send_refuses_typed_inference(system):
    class Blocked:
        blocked, until = "budget_exhausted", datetime(2026, 11, 6, tzinfo=UTC)

    class Closed(Availability):
        def available(self):
            return False

        def blocking(self):
            return Blocked()

    async def scenario(backend, gateway, ctx, hermes):
        client = {"id": "softmax-play", "kind": "web", "guide": "client-play"}
        reply = await call(backend, "session.open", {"scope": WORKSPACE, "client": client})
        session_id = reply["result"]["session"]["id"]
        assert system[2]._clients[session_id] == client
        default = await opened(backend)
        assert system[2]._clients[default] == {"id": "aide", "kind": "ide"}
        refused = await call(
            backend,
            "chat.send",
            {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE},
        )
        assert refused["error"]["data"] == {
            "code": "inference_unavailable",
            "reason": "budget_exhausted",
            "until": "2026-11-06T00:00:00+00:00",
        }
        reads = {"session_id": session_id, "scope": WORKSPACE, "wake": False}
        assert "result" in await call(backend, "chat.history", reads)

    run(system, scenario, inference=Closed())


def test_scope_mismatches_are_forbidden_and_unscoped_sessions_are_hidden(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        coworld_id = await opened(backend, LEAGUE)
        # A Coworld session without a league has no AIDE scope.
        hidden = await backend.seam.post(
            "/session-open",
            json={
                "op_id": str(uuid4()),
                "client": {"id": "legacy", "kind": "ide"},
                "session_context": {
                    "contract_version": 1,
                    "scope": "coworld",
                    "coworld": COWORLD_ID,
                },
            },
        )
        hidden_id = hidden.json()["session_id"]
        for method, params in (
            ("chat.send", {"session_id": session_id, "messages": say("x"), "scope": LEAGUE}),
            ("chat.history", {"session_id": session_id, "scope": LEAGUE}),
            ("state.get", {"resource": "sessions", "id": coworld_id, "scope": WORKSPACE}),
            ("state.get", {"resource": "sessions", "id": hidden_id, "scope": LEAGUE}),
        ):
            assert code(await call(backend, method, params)) == "forbidden", method
        listed = await call(backend, "state.list", {"resource": "sessions"})
        assert {s["id"] for s in listed["result"]["items"]} == {session_id, coworld_id}
        scoped = await call(backend, "state.list", {"resource": "sessions", "scope": LEAGUE})
        assert [s["id"] for s in scoped["result"]["items"]] == [coworld_id]
        bad = {"session_id": session_id, "messages": say("a") + say("b"), "scope": WORKSPACE}
        assert code(await call(backend, "chat.send", bad)) == "unsupported"
        unscoped = await call(
            backend, "state.list", {"resource": "requests", "session_id": session_id}
        )
        assert unscoped["error"]["code"] == -32602

    run(system, scenario)


def test_a_request_whose_session_the_gateway_does_not_have_closes_its_log(system):
    """The packaged lost-receipt fixture: a store session with no native one must not end the
    backend, or the workspace crash-loops on every boot that recovers this request's consumer."""
    server = system[2]

    async def scenario(backend, gateway, ctx, hermes):
        with server._deliver_lock:  # the row reaches the dispatcher only in its lost-receipt form
            sessions.register(
                ctx.wstore, HARNESS, session_id="no-native", profile="orchestrator", kind="chat"
            )
            request_id = admissions.enqueue(
                ctx.wstore,
                HARNESS,
                key="no-native",
                session_id="no-native",
                profile="orchestrator",
                text="never replayed",
                origin="client",
            )
            with ctx.wstore.tx(HARNESS) as tx:
                tx.execute(
                    "UPDATE client_admissions SET dispatch_started=1,resolution_required=1,"
                    "error='Lost receipt' WHERE op_id=?",
                    (request_id,),
                )

        def outcome():
            return ctx.wstore.one(
                "SELECT event FROM request_events WHERE request_id=? AND kind='finished'",
                (request_id,),
            )

        await until(outcome)
        assert json.loads(outcome()["event"])["status"] == "interrupted"
        await until(lambda: not backend.consumers)
        history = await call(
            backend, "chat.history", {"session_id": "no-native", "scope": WORKSPACE, "limit": 5}
        )
        assert history["result"]["items"] == []
        # The next backend finds the log closed and starts nothing for it.
        restarted = backend.restart()
        await until(lambda: restarted.start.done())
        assert not restarted.consumers

    run(system, scenario)
    assert not [name for name, _ in system[1].calls if name == "start"]


def test_history_pages_by_a_stable_boundary_reads_forward_and_bounds_each_reply(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        await until(lambda: session_id in hermes.sessions)
        for text in ("a", "b", "c", "d", "e"):
            gateway.say(session_id, role="assistant", content=text)
        params = {"session_id": session_id, "scope": WORKSPACE, "limit": 2}
        pages, cursor_, arrived = [], None, False
        while True:
            page = (
                await call(
                    backend, "chat.history", params | ({"cursor": cursor_} if cursor_ else {})
                )
            )["result"]
            pages.append([i["message"]["content"] for i in page["items"]])
            cursor_ = page["next_cursor"]
            if not arrived:
                # New messages do not move a boundary already issued.
                gateway.say(session_id, role="assistant", content="new")
                arrived = True
            if cursor_ is None:
                break
        # The newest records first, each page oldest first, no record repeated or skipped.
        assert pages == [["d", "e"], ["b", "c"], ["a"]]
        first = await call(backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE})
        sequences = [i["sequence"] for i in first["result"]["items"]]
        assert sequences == sorted(sequences)
        forward = await call(
            backend,
            "chat.history",
            {"session_id": session_id, "scope": WORKSPACE, "after_sequence": sequences[0]},
        )
        assert [i["sequence"] for i in forward["result"]["items"]] == sequences[1:]
        assert forward["result"]["next_cursor"] is None
        for _ in range(3):
            gateway.say(session_id, role="assistant", content="x" * 400_000)
        page = (
            await call(backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE})
        )["result"]
        assert len(page["items"]) == 2 and page["next_cursor"] is not None
        gateway.say(session_id, role="assistant", content="y" * 1_100_000)
        huge = await call(backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE})
        assert code(huge) == "too_large"
        unknown = {"session_id": session_id, "request_id": str(uuid4()), "scope": WORKSPACE}
        assert code(await call(backend, "chat.cancel", unknown)) == "not_found"

    run(system, scenario)


def test_history_marks_continuations_failed_turns_and_reasoning():
    rows = [
        aide.NativeMessage(id=1, role="user", content="hi", client_context={"view": "x"}),
        aide.NativeMessage(id=2, role="user", content=run_failures.continuation("rate_limited")),
        aide.NativeMessage(id=3, role="assistant", content="Sorry", display_kind="failed_turn"),
        aide.NativeMessage(id=4, role="assistant", content="Ok", reasoning_content="Because"),
        aide.NativeMessage(id=5, role="assistant", content="", display_kind="hidden"),
    ]
    records = [aide.record(row, "s") for row in rows]
    assert [r and r.kind for r in records] == [
        "message",
        "continuation",
        "failed_turn",
        "message",
        None,
    ]
    assert records[3].model_dump(mode="json") == {
        "id": "4",
        "session_id": "s",
        "sequence": 4,
        "message": {"role": "assistant", "content": "Ok"},
        "kind": "message",
        "reasoning": "Because",
    }


def test_tasks_goals_cancellation_and_pending_input(system):
    async def scenario(backend, gateway, ctx, hermes):
        goal = goals.create(ctx.store, HARNESS, title="Win", end_state="Top", reason="asked")
        task = tasks.create(
            ctx.store,
            HARNESS,
            type="maintenance",
            title="Fix",
            description="Do",
            goal_id=goal["id"],
        )
        listed = await call(backend, "state.list", {"resource": "tasks", "scope_id": COWORLD_ID})
        (item,) = listed["result"]["items"]
        assert item["id"] == f"{COWORLD_ID}:{task['id']}" and item["status"] == "pending"
        assert item["goal_id"] == f"{COWORLD_ID}:{goal['id']}"
        got = await call(
            backend,
            "state.get",
            {"resource": "goals", "id": f"{COWORLD_ID}:{goal['id']}", "scope": LEAGUE},
        )
        assert got["result"]["item"]["status"] == "active"
        assert code(await call(backend, "state.list", {"resource": "goals"})) == "unsupported"
        cancel = {"task_id": item["id"], "scope": LEAGUE}
        assert (await call(backend, "task.cancel", cancel))["result"] == {"requested": True}
        assert (await call(backend, "task.cancel", cancel))["result"] == {"requested": False}
        assert (
            code(await call(backend, "task.cancel", {**cancel, "scope": WORKSPACE})) == "forbidden"
        )
        unregistered = {**LEAGUE, "coworld": "cow_unregistered"}
        stray = {"task_id": "cow_unregistered:T-1", "scope": unregistered}
        assert code(await call(backend, "task.cancel", stray)) == "not_found"

        session_id = await opened(backend)
        hermes.script = lambda run: gateway.controls.__setitem__(
            run.run_id,
            [
                {
                    "kind": "approval",
                    "prompt_id": "p1",
                    "execution_generation": 3,
                    "description": "Delete the scratch folder",
                    "command": "rm -rf scratch",
                    "choices": ["once", "session", "always", "deny"],
                }
            ],
        )
        gateway.script = lambda run_id: [{"event": "run.started"}, {"gate": True}]
        params = {"session_id": session_id, "messages": say("go"), "scope": WORKSPACE}
        await call(backend, "chat.send", params)
        state = {"resource": "sessions", "id": session_id, "scope": WORKSPACE}
        await until(lambda: hermes.runs)
        while not (await call(backend, "state.get", state))["result"]["item"]["pending"]:
            await asyncio.sleep(0.05)
        (pending,) = (await call(backend, "state.get", state))["result"]["item"]["pending"]
        (run_id,) = hermes.runs
        expected = uuid5(NAMESPACE_URL, f"aide-input:{session_id}:{run_id}:3:p1")
        assert pending == {
            "request_id": str(expected),
            "prompt": "Delete the scratch folder",
            "kind": "approval",
            "choices": ["once", "deny"],
            "description": "Delete the scratch folder",
            "command": "rm -rf scratch",
        }
        answer = {"session_id": session_id, "request_id": pending["request_id"], "scope": WORKSPACE}
        wrong = await call(
            backend,
            "input.respond",
            answer | {"response": {"kind": "clarification", "answer": "x"}},
        )
        assert code(wrong) == "conflict"
        once = answer | {"response": {"kind": "approval", "choice": "once"}}
        respond_id = str(uuid4())
        applied = await call(backend, "input.respond", once, request_id=respond_id)
        assert applied["result"] == {"applied": True}
        # An identical retry replays the result, also from a rebuilt backend, without answering
        # the resolved prompt again; other input under that id is refused.
        again = await call(backend, "input.respond", once, request_id=respond_id)
        assert again["result"] == {"applied": True}
        backend = backend.restart()
        again = await call(backend, "input.respond", once, request_id=respond_id)
        assert again["result"] == {"applied": True}
        deny = answer | {"response": {"kind": "approval", "choice": "deny"}}
        reused = await call(backend, "input.respond", deny, request_id=respond_id)
        assert code(reused) == "id_reused"
        assert gateway.answers == [
            ("approval", {"request_id": "p1", "execution_generation": 3, "choice": "once"})
        ]
        stale = await call(
            backend, "input.respond", answer | {"response": {"kind": "approval", "choice": "deny"}}
        )
        assert code(stale) == "conflict"
        gateway.gate.set()

    run(system, scenario)


def test_pending_clarification_has_no_choices_field(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        hermes.script = lambda run: gateway.controls.__setitem__(
            run.run_id,
            [
                {
                    "kind": "clarify",
                    "prompt_id": "q1",
                    "execution_generation": 1,
                    "question": "Which league?",
                }
            ],
        )
        gateway.script = lambda run_id: [{"event": "run.started"}, {"gate": True}]
        params = {"session_id": session_id, "messages": say("go"), "scope": WORKSPACE}
        await call(backend, "chat.send", params)
        state = {"resource": "sessions", "id": session_id, "scope": WORKSPACE}
        await until(lambda: hermes.runs)
        while not (await call(backend, "state.get", state))["result"]["item"]["pending"]:
            await asyncio.sleep(0.05)
        (pending,) = (await call(backend, "state.get", state))["result"]["item"]["pending"]
        (run_id,) = hermes.runs
        # The contract's clarification variant forbids extra properties, including a null `choices`.
        assert pending == {
            "request_id": str(uuid5(NAMESPACE_URL, f"aide-input:{session_id}:{run_id}:1:q1")),
            "prompt": "Which league?",
            "kind": "clarification",
        }
        gateway.gate.set()

    run(system, scenario)


def test_frontend_closed_ends_its_subscriptions(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        await call(
            backend,
            "state.get",
            {"resource": "sessions", "id": session_id, "scope": WORKSPACE},
            frontend=OTHER,
        )
        backend.closed(OTHER)
        assert backend.subscribers[session_id].frontends == {FRONTEND}

    run(system, scenario)


def test_relay_failures_reconnect_without_touching_the_legacy_connection():
    from control import serve as legacy
    from websockets.asyncio.server import serve as websocket_server

    async def main():
        attempts, legacy_connections, bound = [], [], asyncio.Event()
        binding = {"backend_id": str(uuid4()), "version": "1.0.0", "max_message_bytes": 1048576}

        async def platform(socket):
            if socket.request.path.endswith("/connection"):
                legacy_connections.append(socket)
                await socket.wait_closed()
                return
            attempts.append(socket.request.headers["Authorization"])
            if len(attempts) == 1:
                await socket.close(code=1008)  # superseded or expired: reconnect
                return
            assert socket.subprotocol == "aide.1.0.0"
            await socket.send(json.dumps({"binding": binding}))
            bound.set()
            await socket.wait_closed()

        async with websocket_server(
            platform, "127.0.0.1", 0, select_subprotocol=lambda _, offered: (offered or [None])[0]
        ) as server:
            url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            common = {"platform_url": url, "workspace_id": "w", "credential": "proof"}
            env = {"API_SERVER_KEY": "proof"}
            connected = asyncio.Event()
            control = asyncio.create_task(
                legacy(**common, env=env, inference=Availability(), connected=connected)
            )
            backend = asyncio.create_task(aide.serve(**common, env=env, inference=Availability()))
            async with asyncio.timeout(10):
                await bound.wait()
                await connected.wait()
            assert len(legacy_connections) == 1
            assert attempts == ["Bearer proof", "Bearer proof"]
            assert not control.done() and not backend.done()
            backend.cancel()
            control.cancel()

    asyncio.run(main())
