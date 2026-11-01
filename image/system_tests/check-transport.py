"""Exercise bridge failures over real HTTP and websocket connections, without model credentials."""

import asyncio
import base64
import json
import sys
from pathlib import Path
from uuid import uuid4

from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import serve as bridge  # noqa: E402
from inference import Availability  # noqa: E402


async def main():
    fail_stream = asyncio.Event()
    held_started = asyncio.Event()
    release_held = asyncio.Event()
    finished = asyncio.Event()
    connections = 0

    async def gateway(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        path = headers.split(b" ")[1]
        if path.endswith(b"/broken/events?last_seq=0"):
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            )
            chunk = b'data: {"event":"run.started","run_id":"broken","seq":1}\n\n'
            writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            await writer.drain()
            await fail_stream.wait()
            # Deliberately omit the terminating chunk: a real HTTP transport failure after headers.
        elif not path.endswith(b"/unavailable"):
            run_id = "held" if path.endswith(b"/held") else "after"
            if run_id == "held":
                held_started.set()
                await release_held.wait()
            body = json.dumps({"run_id": run_id, "status": "completed"}).encode()
            writer.write(
                (
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
        # /unavailable closes before headers, also without substituting an application failure.
        writer.close()
        await writer.wait_closed()

    async def operation(socket, name, run_id, stream_id):
        request_id = str(uuid4())
        await socket.send(
            json.dumps(
                {
                    "stream_id": stream_id,
                    "envelope": {
                        "id": request_id,
                        "op_id": str(uuid4()),
                        "name": name,
                        "args": {"run_id": run_id},
                    },
                }
            )
        )
        return request_id

    async def reply(socket, request_id):
        frames = [json.loads(await socket.recv())]
        while not frames[-1]["end"]:
            frames.append(json.loads(await socket.recv()))
        assert all(frame["request_id"] == request_id for frame in frames)
        assert [frame["seq"] for frame in frames] == list(range(len(frames)))
        assert (
            json.loads(b"".join(base64.b64decode(frame["data"]) for frame in frames))["status"]
            == "completed"
        )

    async def platform(socket):
        nonlocal connections
        connections += 1
        broken = await operation(socket, "run.events", "broken", "1-0")
        header, data = json.loads(await socket.recv()), json.loads(await socket.recv())
        assert header["request_id"] == broken and header["seq"] == 0
        assert data["request_id"] == broken and data["seq"] == 1
        held = await operation(socket, "run.get", "held", "2-0")
        await held_started.wait()
        fail_stream.set()
        assert json.loads(await socket.recv()) == {
            "request_id": broken,
            "stream_id": "1-0",
            "aborted": True,
        }
        unavailable = await operation(socket, "run.get", "unavailable", "3-0")
        assert json.loads(await socket.recv()) == {
            "request_id": unavailable,
            "stream_id": "3-0",
            "aborted": True,
        }
        release_held.set()
        await reply(socket, held)
        after = await operation(socket, "run.get", "after", "4-0")
        await reply(socket, after)
        finished.set()
        await socket.wait_closed()

    async with (
        await asyncio.start_server(gateway, "127.0.0.1", 8642),
        serve(platform, "127.0.0.1", 0) as server,
    ):
        port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(
            bridge(
                platform_url=f"http://127.0.0.1:{port}",
                workspace_id=str(uuid4()),
                credential="proof",
                env={"API_SERVER_KEY": "proof"},
                inference=Availability(),
            )
        )
        try:
            async with asyncio.timeout(10):
                await finished.wait()
            assert connections == 1 and not task.done(), (
                "A failed request restarted the shared connection"
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    print("PASS request aborts preserve concurrent replies and the shared websocket")


asyncio.run(main())
