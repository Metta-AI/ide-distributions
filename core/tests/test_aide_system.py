"""Canonical command receipt fault boundary through the real HTTP seam and retained database."""

import asyncio
import importlib.util
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from research_core.serve import SeamServer
from research_core.watcher.hermes_api import FakeHermesRuns
from tests.test_serve import make_ctx

spec = importlib.util.spec_from_file_location(
    "native_aide", Path(__file__).resolve().parents[2] / "image/aide.py"
)
aide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aide)


def test_lost_control_ack_is_unknown_after_restart_and_never_redispatched(tmp_path):
    ctx = make_ctx(tmp_path, coworld=False)
    server = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns(), env=dict(ctx.env))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    calls = []
    request = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "input.respond",
        "params": {
            "session_id": "s",
            "scope": {"scope": "workspace", "coworld": None, "league": None},
            "request_id": str(uuid4()),
            "response": {"kind": "approval", "choice": "once"},
        },
    }

    async def lost_ack():
        async with httpx.AsyncClient(base_url=server.url) as native:
            app = aide.Application(native, native)

            async def apply_then_lose_ack(_):
                calls.append("native applied")
                raise httpx.ReadError("Native response lost after application")

            app.execute_validated = apply_then_lose_ack
            with pytest.raises(httpx.ReadError):
                await app.execute(request)

    asyncio.run(lost_ack())
    server.shutdown()
    worker.join(timeout=10)
    assert not worker.is_alive()
    restarted = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns(), env=dict(ctx.env))
    threading.Thread(target=restarted.serve_forever, daemon=True).start()

    async def recover():
        async with httpx.AsyncClient(base_url=restarted.url) as native:
            app = aide.Application(native, native)

            async def must_not_dispatch(_):
                calls.append("unexpected second native application")
                raise AssertionError("An uncertain native control was redispatched")

            app.execute_validated = must_not_dispatch
            reply = await app.execute(request)
            assert reply["error"]["data"] == {"code": "unavailable", "outcome": "unknown"}
            changed = {
                **request,
                "params": {**request["params"], "response": {"kind": "approval", "choice": "deny"}},
            }
            assert (await app.execute(changed))["error"]["data"]["code"] == "id_reused"

    try:
        asyncio.run(recover())
        assert calls == ["native applied"]
    finally:
        restarted.shutdown()


def test_lost_open_result_recovers_durable_session_after_restart(tmp_path):
    ctx = make_ctx(tmp_path, coworld=False)
    server = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns(), env=dict(ctx.env))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    request = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "session.open",
        "params": {"scope": {"scope": "workspace", "coworld": None, "league": None}},
    }

    async def lose_open_response():
        async def lose_response(response):
            if response.request.url.path == "/session-open":
                response.raise_for_status()
                raise httpx.ReadError("Lost opening ACK after durable native acceptance")

        async with httpx.AsyncClient(
            base_url=server.url, event_hooks={"response": [lose_response]}
        ) as native:
            app = aide.Application(native, native)
            with pytest.raises(httpx.ReadError):
                await app.execute(request)

    asyncio.run(lose_open_response())
    server.shutdown()
    worker.join(timeout=10)
    assert not worker.is_alive()
    accepted = ctx.wstore.one(
        "SELECT session_id FROM client_admissions WHERE op_id=?", (request["id"],)
    )[0]
    restarted = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns(), env=dict(ctx.env))
    restarted_worker = threading.Thread(target=restarted.serve_forever, daemon=True)
    restarted_worker.start()

    async def recover():
        async with httpx.AsyncClient(base_url=restarted.url) as native:
            app = aide.Application(native, native)
            reply = await app.execute(request)
            assert reply["session"]["id"] == accepted
            assert await app.execute(request) == reply
            changed = {
                **request,
                "params": {
                    "scope": {"scope": "coworld", "coworld": "cow_other", "league": "league_other"}
                },
            }
            assert (await app.execute(changed))["error"]["data"]["code"] == "id_reused"

    try:
        asyncio.run(recover())
        assert ctx.wstore.one("SELECT COUNT(*) FROM client_admissions WHERE kind='open'")[0] == 1
        assert ctx.wstore.one("SELECT COUNT(*) FROM sessions WHERE kind='chat'")[0] == 1
    finally:
        restarted.shutdown()
        restarted_worker.join(timeout=10)


def test_wrong_scope_refuses_content_and_pending_controls_before_native_access(tmp_path):
    import json

    server_seam = make_ctx(tmp_path, coworld=False)
    server = SeamServer(
        ws=server_seam.workspace, hermes=FakeHermesRuns(), env=dict(server_seam.env)
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    opened = server.session_open(
        {
            "op_id": str(uuid4()),
            "session_id": "private",
            "client": {"id": "test", "kind": "test"},
            "session_context": {
                "contract_version": 1,
                "scope": "workspace",
                "coworld": None,
                "league": None,
            },
        }
    )
    server.message(
        {
            "op_id": str(uuid4()),
            "session_id": opened["session_id"],
            "message": {"role": "user", "content": "private evidence"},
        }
    )
    server.aide_pending(
        {
            "session_id": "private",
            "pending": [
                {
                    "request_id": str(uuid4()),
                    "kind": "approval",
                    "prompt": "private pending",
                    "choices": ["once", "deny"],
                }
            ],
        }
    )

    def deny_native(request):
        raise AssertionError("Wrong-scope request reached native content/control")

    async def check():
        scope = {"scope": "coworld", "coworld": "cow_other", "league": "league_other"}
        async with (
            httpx.AsyncClient(base_url=server.url) as native,
            httpx.AsyncClient(
                base_url="http://native.invalid/p/orchestrator/",
                transport=httpx.MockTransport(deny_native),
            ) as gateway,
        ):
            app = aide.Application(native, gateway)
            for method, params in [
                ("chat.history", {"session_id": "private"}),
                (
                    "chat.send",
                    {"session_id": "private", "messages": [{"role": "user", "content": "Attempt"}]},
                ),
                ("chat.cancel", {"session_id": "private", "request_id": str(uuid4())}),
                (
                    "input.respond",
                    {
                        "session_id": "private",
                        "request_id": str(uuid4()),
                        "response": {"kind": "approval", "choice": "once"},
                    },
                ),
                ("state.get", {"resource": "sessions", "id": "private"}),
            ]:
                reply = await app.execute(
                    {
                        "jsonrpc": "2.0",
                        "id": str(uuid4()),
                        "method": method,
                        "params": {"scope": scope, **params},
                    }
                )
                assert reply["error"]["data"]["code"] == "forbidden"
                assert "private evidence" not in json.dumps(
                    reply
                ) and "private pending" not in json.dumps(reply)

    try:
        asyncio.run(check())
        assert (
            server_seam.wstore.one("SELECT COUNT(*) FROM client_admissions WHERE kind='message'")[0]
            == 1
        )
        assert server_seam.wstore.one("SELECT SUM(cancel_requested) FROM client_admissions")[0] == 0
    finally:
        server.shutdown()
        worker.join(timeout=10)


def test_worker_pending_inputs_and_responses_use_own_native_profile(tmp_path):
    import json

    from research_core.store import sessions, tasks
    from research_core.store.actors import Actor

    ctx = make_ctx(tmp_path)
    scope = {"scope": "coworld", "coworld": "cow_fake", "league": "league_fake"}
    task = tasks.create(
        ctx.store,
        Actor.harness(),
        type="maintenance",
        title="Worker",
        description="Profile routing fixture",
    )
    sessions.register(
        ctx.wstore,
        Actor.harness(),
        session_id="worker",
        profile="researcher",
        kind="task",
        task_id=int(task["id"].removeprefix("T-")),
        coworld_id="cow_fake",
        session_context=scope,
    )
    sessions.set_run(ctx.wstore, Actor.harness(), "worker", "worker-run")
    server = SeamServer(ws=ctx.workspace, hermes=FakeHermesRuns(), env=dict(ctx.env))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    calls = []

    def native(request):
        calls.append(request)
        assert request.url.path.startswith("/p/researcher/v1/runs/worker-run")
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pending_controls": [
                        {
                            "kind": "approval",
                            "prompt_id": "native-prompt",
                            "execution_generation": 7,
                            "description": "Worker approval",
                            "choices": ["once", "deny"],
                        }
                    ]
                },
            )
        assert request.url.path.endswith("/approval")
        assert json.loads(request.content) == {
            "request_id": "native-prompt",
            "execution_generation": 7,
            "choice": "once",
        }
        return httpx.Response(200, json={"status": "resolved"})

    async def check():
        async with (
            httpx.AsyncClient(base_url=server.url) as seam,
            httpx.AsyncClient(
                base_url="http://native.invalid/p/orchestrator/",
                transport=httpx.MockTransport(native),
            ) as gateway,
        ):
            app = aide.Application(seam, gateway)
            selected = await app.execute(
                {
                    "jsonrpc": "2.0",
                    "id": str(uuid4()),
                    "method": "state.get",
                    "params": {"resource": "sessions", "id": "worker", "scope": scope},
                }
            )
            pending = selected["item"]["pending"]
            assert len(pending) == 1 and pending[0]["prompt"] == "Worker approval"
            response = {
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": "input.respond",
                "params": {
                    "session_id": "worker",
                    "scope": scope,
                    "request_id": pending[0]["request_id"],
                    "response": {"kind": "approval", "choice": "once"},
                },
            }
            assert await app.execute(response) == {"applied": True}
            posts = len([request for request in calls if request.method == "POST"])
            assert await app.execute(response) == {"applied": True}
            assert len([request for request in calls if request.method == "POST"]) == posts == 1

    try:
        asyncio.run(check())
    finally:
        server.shutdown()
        worker.join(timeout=10)


def test_canonical_ordered_content_arrays_are_json_serialized_at_durable_intake(
    tmp_path, monkeypatch
):
    import json

    ctx = make_ctx(tmp_path, coworld=False)
    from research_core.profile_models import Profile

    server = SeamServer(
        ws=ctx.workspace,
        hermes=FakeHermesRuns(),
        env=dict(ctx.env),
        profiles=[
            Profile(
                "orchestrator", "orchestrator", Path(__file__).resolve().parents[2] / "orchestrator"
            )
        ],
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    scope = {"scope": "workspace", "coworld": None, "league": None}
    request_id = str(uuid4())
    messages = [
        {"role": "developer", "content": "Context"},
        {"role": "user", "content": [{"type": "text", "text": "Visible input"}]},
    ]

    async def check():
        async with httpx.AsyncClient(base_url=server.url) as native:
            app = aide.Application(native, native)
            opened = await app.execute(
                {
                    "jsonrpc": "2.0",
                    "id": str(uuid4()),
                    "method": "session.open",
                    "params": {"scope": scope},
                }
            )
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "chat.send",
                "params": {
                    "session_id": opened["session"]["id"],
                    "scope": scope,
                    "messages": messages,
                },
            }
            reply = await app.execute(request)
            assert reply["accepted"]
            monkeypatch.setattr(aide, "supported_messages", lambda _: False)
            assert await app.execute(request) == reply

    try:
        asyncio.run(check())
        payload = ctx.wstore.one(
            "SELECT payload FROM client_admissions WHERE op_id=?", (request_id,)
        )[0]
        assert json.loads(payload)["input"] == messages
    finally:
        server.shutdown()
        worker.join(timeout=10)
