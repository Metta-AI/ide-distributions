"""Canonical requests multiplex through real websocket and HTTP peers after a request failure."""

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aide import serve as backend  # noqa: E402
from inference import Availability  # noqa: E402


async def main():
    held_started, release_held, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    connected = asyncio.Event()
    connections = 0
    scope = {"scope": "workspace", "coworld": None, "league": None}

    async def seam(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        path = headers.split(b" ")[1].decode()
        session_id = parse_qs(urlsplit(path).query).get("session_id", [""])[0]
        if session_id == "broken":
            writer.close()
            await writer.wait_closed()
            return
        if session_id == "held":
            held_started.set()
            await release_held.wait()
        body = (
            {"items": []}
            if path == "/aide-retired"
            else {"revision": 1}
            if path == "/aide-pending"
            else {
                "store": "workspace",
                "seq": 1,
                "records": {
                    "admissions": [],
                    "sessions": [
                        {
                            "id": session_id,
                            "profile": "orchestrator",
                            "run_id": None,
                            "parent": None,
                            "started": "2026-11-04T00:00:00Z",
                            "ended": None,
                            "coworld_id": None,
                            "session_context": scope,
                            "pending_inputs": [],
                        }
                    ],
                },
            }
        )
        encoded = json.dumps(body).encode()
        writer.write(
            (
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + encoded
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def platform(socket):
        nonlocal connections
        connections += 1
        await socket.send(
            json.dumps(
                {
                    "binding": {
                        "backend_id": str(uuid4()),
                        "version": "1.0.0",
                        "max_message_bytes": 1048576,
                    }
                }
            )
        )
        frontend_id = str(uuid4())
        ids = {name: str(uuid4()) for name in ("held", "broken", "after")}

        async def send(name):
            await socket.send(
                json.dumps(
                    {
                        "frontend_id": frontend_id,
                        "message": {
                            "jsonrpc": "2.0",
                            "id": ids[name],
                            "method": "state.get",
                            "params": {"resource": "sessions", "id": name, "scope": scope},
                        },
                    }
                )
            )

        if connections == 2:
            await send("after")
            recovered = json.loads(await socket.recv())["message"]
            assert recovered["result"]["item"]["id"] == "after"
            finished.set()
            await socket.wait_closed()
            return
        await send("held")
        await held_started.wait()
        await send("broken")
        broken = json.loads(await socket.recv())["message"]
        assert broken["id"] == ids["broken"] and broken["error"]["data"] == {
            "code": "unavailable",
            "outcome": "unknown",
        }, broken
        await send("after")
        after = json.loads(await socket.recv())["message"]
        assert after["id"] == ids["after"] and after["result"]["item"]["id"] == "after", after
        release_held.set()
        held = json.loads(await socket.recv())["message"]
        assert held["id"] == ids["held"] and held["result"]["item"]["id"] == "held", held
        socket.transport.abort()

    http = await asyncio.start_server(seam, "127.0.0.1", 8650)
    async with http, serve(platform, "127.0.0.1", 0, subprotocols=["aide.1.0.0"]) as websocket:
        port = websocket.sockets[0].getsockname()[1]
        task = asyncio.create_task(
            backend(
                platform_url=f"http://127.0.0.1:{port}",
                workspace_id=str(uuid4()),
                credential="proof",
                env={"API_SERVER_KEY": "proof"},
                connected=connected,
                inference=Availability(),
            )
        )
        try:
            async with asyncio.timeout(10):
                await finished.wait()
            assert connected.is_set() and connections == 2 and not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    print("PASS canonical request failure preserves multiplexing; abrupt callback loss reconnects")


asyncio.run(main())
