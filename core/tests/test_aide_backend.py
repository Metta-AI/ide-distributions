"""The image's AIDE 1.0.0 backend over a real seam and a scripted gateway."""

import asyncio
import json
import sys
import threading
import types
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest

from research_core.serve import SeamServer
from research_core.store import goals, settings, tasks
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
    """The gateway routes the backend reads: transcripts, run state and run event streams."""

    def __init__(self, hermes: FakeHermesRuns):
        self.hermes = hermes
        self.transcripts: dict[str, list[dict]] = {}
        self.controls: dict[str, list[dict]] = {}
        self.event_requests: list[tuple[str, str]] = []
        self.drop_after: int | None = None
        self.answers: list[tuple[str, dict]] = []

    def say(self, session_id: str, **message) -> None:
        rows = self.transcripts.setdefault(session_id, [])
        rows.append({"id": 100 + sum(len(t) for t in self.transcripts.values()), **message})

    def script(self, run_id: str) -> list[dict]:
        session_id = self.hermes.runs[run_id].session_id
        call = {"id": "call_1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
        return [
            {"event": "run.started"},
            {"event": "message.delta", "delta": "Hel"},
            {"event": "message.delta", "delta": "lo"},
            {"event": "tool.started", "say": [(session_id, "assistant", None, [call], None)]},
            {"event": "tool.completed", "say": [(session_id, "tool", "files", None, "call_1")]},
            {"event": "message.delta", "delta": "Done"},
            {"event": "run.completed", "say": [(session_id, "assistant", "Done", None, None)]},
        ]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/p/orchestrator/")
        if path.startswith("api/sessions/"):
            session_id = path.split("/")[2]
            if session_id not in self.hermes.sessions:
                return httpx.Response(404, json={"error": "Session not found"})
            rows = list(reversed(self.transcripts.get(session_id, [])))
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(200, json={"data": rows[offset : offset + limit]})
        run_id = path.split("/")[2]
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
        for seq, event in enumerate(self.script(run_id)):
            if seq <= last_seq:
                continue
            if self.drop_after is not None and seq > self.drop_after:
                self.drop_after = None
                raise httpx.ReadError("gateway stream dropped")
            for session_id, role, content, calls, call_id in event.pop("say", []):
                message = {"role": role, "content": content}
                message |= {"tool_calls": calls} if calls else {}
                message |= {"tool_call_id": call_id} if call_id else {}
                self.say(session_id, **message)
            if event["event"] == "run.completed":
                self.hermes.complete(run_id, "Done")
            payload = {**event, "run_id": run_id, "seq": seq, "timestamp": 1.0}
            yield f"id: {seq}\ndata: {json.dumps(payload)}\n\n".encode()
            await asyncio.sleep(0)
        yield b": stream closed\n\n"


@pytest.fixture
def system(tmp_path):
    ctx = make_ctx(tmp_path, coworld=True)
    hermes = FakeHermesRuns()
    server = SeamServer(ws=ctx.workspace, hermes=hermes, env=dict(ctx.env))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield ctx, hermes, server
    server.shutdown()
    worker.join(timeout=10)


def run(system, scenario):
    ctx, hermes, server = system
    gateway = Gateway(hermes)

    async def main():
        async with (
            httpx.AsyncClient(base_url=server.url) as seam,
            httpx.AsyncClient(
                base_url="http://gateway/p/orchestrator/",
                transport=httpx.MockTransport(gateway.handle),
            ) as native,
            asyncio.TaskGroup() as work,
        ):
            backend = aide.Backend(seam, native, Availability(), work)
            backend.socket = Socket()
            watcher = work.create_task(backend.watch())
            try:
                async with asyncio.timeout(30):
                    await scenario(backend, gateway, ctx, hermes)
            finally:
                watcher.cancel()
                for bridge in list(backend.bridges.values()):
                    bridge.cancel()

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


def test_chat_round_trip_streams_chunks_messages_and_outcome_to_every_subscriber(system):
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
        for frontend in (FRONTEND, OTHER):
            chunks = socket.notes("chat.chunk", frontend)
            assert [(c["completion_id"].rsplit(":", 1)[1], c["seq"]) for c in chunks] == [
                ("0", 1),
                ("0", 2),
                ("1", 1),
            ]
            assert "".join(c["chunk"]["choices"][0]["delta"]["content"] for c in chunks) == (
                "HelloDone"
            )
            messages = socket.notes("chat.message", frontend)
            assert [m["message"]["role"] for m in messages] == ["assistant", "tool", "assistant"]
            assert all(m["request_id"] == request_id for m in messages)
            assert socket.notes("chat.finished", frontend) == [
                {"session_id": session_id, "request_id": request_id, "status": "completed"}
            ]
            changes = socket.notes("state.changed", frontend)
            assert any(request_id in c["item"]["active_request_ids"] for c in changes)
            assert all(c["previous_revision"] + 1 == c["revision"] for c in changes)
        history = await call(
            backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE}
        )
        items = history["result"]["items"]
        assert [i["message"]["role"] for i in items] == ["assistant", "tool", "assistant"]
        assert [i["sequence"] for i in items] == sorted(i["sequence"] for i in items)
        assert history["result"]["next_cursor"] is None
        # A retry of the accepted request returns the original acceptance and starts nothing.
        again = await call(backend, "chat.send", params, request_id=request_id)
        assert again["result"] == accepted["result"]
        changed = {**params, "messages": say("other")}
        assert code(await call(backend, "chat.send", changed, request_id=request_id)) == "id_reused"

    run(system, scenario)
    ctx, hermes, _ = system
    assert [name for name, _ in hermes.calls].count("start") == 1


def test_a_dropped_run_stream_resumes_from_its_last_sequence_without_duplicates(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        gateway.drop_after = 1
        params = {"session_id": session_id, "messages": say("hi"), "scope": WORKSPACE}
        await call(backend, "chat.send", params)
        await until(lambda: backend.socket.notes("chat.finished"))
        assert [last for _, last in gateway.event_requests] == ["-1", "1"]
        deltas = [
            c["chunk"]["choices"][0]["delta"]["content"] for c in backend.socket.notes("chat.chunk")
        ]
        assert deltas == ["Hel", "lo", "Done"]

    run(system, scenario)


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

    run(system, scenario)


def test_history_pages_newest_first_and_bounds_each_reply_to_one_mebibyte(system):
    async def scenario(backend, gateway, ctx, hermes):
        session_id = await opened(backend)
        await until(lambda: session_id in hermes.sessions)
        for text in ("a", "b", "c", "d", "e"):
            gateway.say(session_id, role="assistant", content=text)
        params = {"session_id": session_id, "scope": WORKSPACE, "limit": 2}
        seen, cursor = [], None
        while True:
            page = (
                await call(backend, "chat.history", params | ({"cursor": cursor} if cursor else {}))
            )["result"]
            seen = [i["message"]["content"] for i in page["items"]] + seen
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == ["a", "b", "c", "d", "e"]
        for _ in range(3):
            gateway.say(session_id, role="assistant", content="x" * 400_000)
        page = (
            await call(backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE})
        )["result"]
        assert len(page["items"]) == 2 and page["next_cursor"] == "2"
        gateway.say(session_id, role="assistant", content="y" * 1_100_000)
        huge = await call(backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE})
        assert code(huge) == "too_large"
        # An accepted message shows before the dispatcher hands it to the gateway.
        settings.update(ctx.wstore, HARNESS, "paused", True, cites="hold dispatch")
        request_id = str(uuid4())
        sent = {"session_id": session_id, "messages": say("queued"), "scope": WORKSPACE}
        await call(backend, "chat.send", sent, request_id=request_id)
        page = (
            await call(
                backend, "chat.history", {"session_id": session_id, "scope": WORKSPACE, "limit": 1}
            )
        )["result"]
        assert page["items"][-1]["request_id"] == request_id
        assert page["items"][-1]["message"] == {"role": "user", "content": "queued"}
        cancelled = await call(
            backend,
            "chat.cancel",
            {"session_id": session_id, "request_id": request_id, "scope": WORKSPACE},
        )
        assert cancelled["result"] == {"requested": True}
        unknown = {"session_id": session_id, "request_id": str(uuid4()), "scope": WORKSPACE}
        assert code(await call(backend, "chat.cancel", unknown)) == "not_found"
        await until(lambda: backend.socket.notes("chat.finished"))
        assert backend.socket.notes("chat.finished")[0]["status"] == "cancelled"

    run(system, scenario)


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

        session_id = await opened(backend)
        hermes.script = lambda run: gateway.controls.__setitem__(
            run.run_id,
            [
                {
                    "kind": "approval",
                    "prompt_id": "p1",
                    "execution_generation": 3,
                    "description": "rm -rf",
                    "choices": ["once", "session", "always", "deny"],
                }
            ],
        )
        gateway.script = lambda run_id: [{"event": "run.started"}]
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
            "prompt": "rm -rf",
            "kind": "approval",
            "choices": ["once", "deny"],
        }
        answer = {"session_id": session_id, "request_id": pending["request_id"], "scope": WORKSPACE}
        wrong = await call(
            backend,
            "input.respond",
            answer | {"response": {"kind": "clarification", "answer": "x"}},
        )
        assert code(wrong) == "conflict"
        applied = await call(
            backend, "input.respond", answer | {"response": {"kind": "approval", "choice": "once"}}
        )
        assert applied["result"] == {"applied": True}
        assert gateway.answers == [
            ("approval", {"request_id": "p1", "execution_generation": 3, "choice": "once"})
        ]
        stale = await call(
            backend, "input.respond", answer | {"response": {"kind": "approval", "choice": "deny"}}
        )
        assert code(stale) == "conflict"

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
