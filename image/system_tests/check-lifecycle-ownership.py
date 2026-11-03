"""Delay an old owner's status read across a successor's claim over the real control websocket."""

import asyncio
import base64
import json
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import serve as bridge  # noqa: E402
from inference import Availability  # noqa: E402


async def main():
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = 0

    async def seam(reader, writer):
        nonlocal requests
        await reader.readuntil(b"\r\n\r\n")
        requests += 1
        if requests == 1:
            started.set()
            await release.wait()
        body = b'{"has_work":false}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def command(socket, epoch, control):
        identity = str(
            UUID(
                "00000000-0000-8000-8400-000000000001"
                if epoch == 1
                else "00000000-0000-8000-8800-000000000002"
            )
        )
        await socket.send(
            json.dumps(
                {
                    "stream_id": identity,
                    "envelope": {"id": identity, "control": control},
                }
            )
        )
        return identity

    async def answer(socket, identity):
        frames = []
        while not frames or not frames[-1]["end"]:
            frames.append(json.loads(await socket.recv()))
            assert frames[-1]["request_id"] == identity
        return json.loads(b"".join(base64.b64decode(frame["data"]) for frame in frames))

    async def platform(socket):
        old = await command(socket, 1, "sleep_request")
        await started.wait()
        current = await command(socket, 2, "status")
        assert (await answer(socket, current))["ready"] is True
        release.set()
        stale = await answer(socket, old)
        assert not stale["ready"] and not stale["ready_for_sleep"]
        later = await command(socket, 1, "sleep_request")
        assert (await answer(socket, later))["detail"] == "Lifecycle owner superseded"
        current = await command(socket, 2, "status")
        assert (await answer(socket, current))["ready"] is True
        # Known wire vectors cross each UUID field boundary and check the receiver's durable token.
        for epoch, identity in [
            (15, "00000000-0000-8000-bc00-00000000000f"),
            (16, "00000000-0000-8001-8000-000000000010"),
            (65535, "00000000-0000-8fff-bc00-00000000ffff"),
            (65536, "00000000-0001-8000-8000-000000010000"),
            (2**32, "00000001-0000-8000-8000-000100000000"),
            (2**48, "00010000-0000-8000-8000-000000000001"),
            (2**64 - 1, "ffffffff-ffff-8fff-bc00-000000000001"),
        ]:
            await socket.send(
                json.dumps(
                    {
                        "stream_id": identity,
                        "envelope": {"id": identity, "control": "status"},
                    }
                )
            )
            assert (await answer(socket, identity))["ready"] is True
            assert Path(home, ".lifecycle-owner").read_text() == str(epoch)
        finished.set()
        await socket.wait_closed()

    with tempfile.TemporaryDirectory(prefix="lifecycle-control-") as home:
        async with (
            await asyncio.start_server(seam, "127.0.0.1", 8650),
            serve(platform, "127.0.0.1", 0) as server,
        ):
            task = asyncio.create_task(
                bridge(
                    platform_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                    workspace_id=str(uuid4()),
                    credential="proof",
                    env={"API_SERVER_KEY": "proof", "HERMES_HOME": home},
                    inference=Availability(),
                    connected=asyncio.Event(),
                )
            )
            try:
                async with asyncio.timeout(10):
                    await finished.wait()
                assert requests == 10, "stale controls must not reach the application"
                assert Path(home, ".lifecycle-owner").read_text() == str(2**64 - 1)
                assert not task.done(), "stale ownership must not restart the shared connection"
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    print("PASS successor fences delayed and repeated stale sleep requests without disconnecting")


asyncio.run(main())
