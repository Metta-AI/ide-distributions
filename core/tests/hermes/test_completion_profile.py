"""Exercise current OpenAI-message HTTP admission and native completion readiness."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("profile", ["orchestrator", "researcher"])
def test_completion_uses_parent_profile(hermes_bin, tmp_path, profile):
    result = subprocess.run(
        [str(hermes_bin.parent / "python"), str(Path(__file__).resolve()), profile],
        env={
            **os.environ,
            "HERMES_HOME": str(tmp_path),
            "HERMES_REAL_HOME": str(tmp_path),
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    import asyncio
    import json
    import socket
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx
    import yaml
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.run import GatewayRunner
    from gateway.session_identity import identity_of
    from hermes_constants import get_hermes_home
    from tools.async_delegation import get_durable_delegation
    from tools.process_registry import process_registry

    from research_core.watcher.hermes_api import HermesRuns

    profile = sys.argv[1]
    home = Path(os.environ["HERMES_HOME"])
    captured = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append(request)
            parent = any(
                tool["function"]["name"] == "delegate_task" for tool in request.get("tools", [])
            )
            if any(message["role"] == "tool" for message in request["messages"]):
                message = {
                    "role": "assistant",
                    "content": "DELEGATION_WAITING" if parent else "CHILD_RESULT_1591",
                }
                finish = "stop"
            else:
                name = "delegate_task" if parent else "terminal"
                args = (
                    {"tasks": [{"goal": "Compute 37*43", "toolsets": ["terminal"]}]}
                    if parent
                    else {"command": "python3 -c 'print(37*43)'"}
                )
                call = {
                    "id": "fixture_" + name,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                message = {"role": "assistant", "content": None, "tool_calls": [call]}
                finish = "tool_calls"
            choice = {"index": 0, "finish_reason": finish, "message": message}
            response = {
                "id": "proof",
                "object": "chat.completion",
                "created": 1,
                "model": "proof",
                "choices": [choice],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
            if request.get("stream"):
                delta = dict(message)
                for index, call in enumerate(delta.get("tool_calls", [])):
                    call["index"] = index
                response["object"] = "chat.completion.chunk"
                response["choices"] = [{"index": 0, "finish_reason": finish, "delta": delta}]
                body = ("data: " + json.dumps(response) + "\n\ndata: [DONE]\n\n").encode()
            else:
                body = json.dumps(response).encode()
            self.send_response(200)
            mime = "text/event-stream" if request.get("stream") else "application/json"
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    provider_url = f"http://127.0.0.1:{provider.server_port}/v1"
    key = "8c839b96b55a4aeda5a0fdb8f1c9a262"
    config = {
        "model": {"default": "proof", "provider": "openai", "base_url": provider_url},
        "platform_toolsets": {"api_server": ["terminal", "delegation"]},
        "terminal": {"backend": "local", "cwd": str(home)},
        "approvals": {"mode": "off"},
        "agent": {"max_turns": 3},
        "fallback_providers": [],
    }
    for path in (home, *(home / "profiles" / role for role in ("orchestrator", "researcher"))):
        path.mkdir(parents=True, exist_ok=True)
        (path / "config.yaml").write_text(yaml.safe_dump(config))
        (path / ".env").write_text(
            f"OPENAI_API_KEY={key}\nOPENAI_BASE_URL={provider_url}\nAPI_SERVER_KEY={key}\n"
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    runner = GatewayRunner(config=GatewayConfig(multiplex_profiles=True))
    api = APIServerAdapter(
        PlatformConfig(enabled=True, extra={"key": key, "host": "127.0.0.1", "port": port})
    )
    api.gateway_runner = runner
    runner.adapters[Platform.API_SERVER] = api

    async def check():
        assert await api.connect(), "Native API listener did not start"
        sid = f"api_openai_completion_{profile}"
        runs = HermesRuns(base_url=f"http://127.0.0.1:{port}", keys={profile: key})
        try:
            await asyncio.to_thread(runs.create_session, sid, profile=profile)
            run_id = await asyncio.to_thread(
                runs.start,
                profile=profile,
                session_id=sid,
                text=[{"role": "user", "content": "Dispatch the bounded computation"}],
            )
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}/p/{profile}",
                headers={"Authorization": f"Bearer {key}"},
            ) as client:
                async with asyncio.timeout(60):
                    while True:
                        status = (await client.get(f"/v1/runs/{run_id}")).json()
                        if status["status"] in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(0.1)
                assert status["status"] == "completed", status
                assert status["output"] == "DELEGATION_WAITING", status
            event = await asyncio.to_thread(process_registry.completion_queue.get, True, 30)
            assert event["type"] == "async_delegation", event
            assert event["parent_session_id"] == sid, event
            async with runner._completion_event_scope(event):
                assert get_hermes_home() == home / "profiles" / profile
                durable = get_durable_delegation(event["delegation_id"])
                source = runner._build_process_event_source(event)
                identity = identity_of(source)
                ready = await runner._completion_delivery_ready(event)
                print(
                    json.dumps(
                        {
                            "profile": profile,
                            "event": event,
                            "durable": durable,
                            "source": vars(source) if source else None,
                            "identity": str(identity),
                            "ready": ready,
                        },
                        default=str,
                    ),
                    flush=True,
                )
                assert durable is not None, event
                assert ready, (profile, "native completion delivery not ready", event)
        finally:
            await api.disconnect()
            runner.close_all_session_db_handles()
            await asyncio.to_thread(provider.shutdown)
            provider.server_close()

    asyncio.run(check())
