"""A provisioning retry must follow the platform's delay before publishing a usable lease."""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inference import Availability, maintain  # noqa: E402


async def main(tmp_path: Path):
    requests = []
    expires = datetime.now(UTC) + timedelta(minutes=5)
    homes = (tmp_path / "host", tmp_path / "profile")
    for home in homes:
        home.mkdir()
        (home / ".env").write_text("PRESERVED=original\nOPENROUTER_API_KEY=expired\n")
    marker = tmp_path / "lease"
    availability = Availability()
    ready = asyncio.Event()

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        request = await reader.readuntil(b"\r\n\r\n")
        assert request.startswith(b"PUT /v2/workspaces/proof/inference HTTP/1.1\r\n")
        assert b"Authorization: Bearer fixture" in request
        requests.append(time.monotonic())
        if len(requests) == 1:
            assert not ready.is_set() and not availability.available() and not marker.exists()
            status = "503 Service Unavailable"
            headers = "Retry-After: 1\r\n"
            body = b'{"kind":"starting"}'
        else:
            status, headers = "200 OK", ""
            body = json.dumps(
                {
                    "key": "fixture-key",
                    "expires_at": expires.isoformat(),
                    "renew_at": expires.isoformat(),
                    "allowance_usd": 1,
                }
            ).encode()
        writer.write(
            (
                f"HTTP/1.1 {status}\r\n{headers}Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        task = asyncio.create_task(
            maintain(
                platform_url=f"http://127.0.0.1:{port}",
                workspace_id="proof",
                credential="fixture",
                homes=homes,
                availability=availability,
                lease_file=marker,
                ready=ready,
            )
        )
        try:
            async with asyncio.timeout(5):
                await ready.wait()
            assert len(requests) == 2
            assert requests[1] - requests[0] >= 1
            print(f"Inference retry interval: {requests[1] - requests[0]:.3f}s")
            assert availability.available()
            assert marker.read_text() == expires.isoformat()
            for home in homes:
                assert dotenv_values(home / ".env") == {
                    "PRESERVED": "original",
                    "OPENROUTER_API_KEY": "fixture-key",
                }
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


with TemporaryDirectory() as directory:
    asyncio.run(main(Path(directory)))
print("PASS inference retry follows server delay and publishes credentials before readiness")
