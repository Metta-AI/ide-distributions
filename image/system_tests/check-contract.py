"""Exercise the packaged public adapter against the running seam, without model credentials."""

import asyncio
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application import Operation, response  # noqa: E402
from inference import Availability  # noqa: E402

from contract import Request  # noqa: E402


async def main():
    assert shutil.which("pdftoppm"), "The packaged native PDF attachment renderer is missing"
    assert version("faster-whisper") == "1.2.1"
    assert Path("/opt/models/whisper-base/model.bin").is_file()
    for role in ("orchestrator", "researcher"):
        config = yaml.safe_load(Path(f"/workspace/host/profiles/{role}/config.yaml").read_text())
        assert config["stt"]["local"]["model"] == "/opt/models/whisper-base"
    inference = Availability()
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam,
        httpx.AsyncClient(
            base_url="http://127.0.0.1:8642/p/orchestrator/",
            headers={
                "Authorization": "Bearer "
                + Path("/workspace/host/.gateway-key").read_text().strip()
            },
        ) as gateway,
    ):
        operation = Operation(
            id=uuid4(),
            op_id=UUID("00000000-0000-4000-8000-000000000124"),
            name="session.open",
            args={"client": {"id": "contract-proof", "kind": "test"}},
        )
        assert Request.model_validate(operation.model_dump(exclude={"id"})).root
        async with response(operation, seam, gateway, inference) as result:
            assert result.status_code == 200, await result.aread()
            session = result.json()["session_id"]
        transcript = operation.model_copy(
            update={
                "op_id": uuid4(),
                "name": "session.messages",
                "args": {"session_id": session, "offset": 0, "limit": 20},
            }
        )
        async with response(transcript, seam, gateway, inference) as result:
            assert result.status_code == 200, await result.aread()
            assert result.json()["data"] == []
        # Recover the native receipt, including the second retained-volume cycle.
        async with response(operation, seam, gateway, inference) as result:
            assert result.status_code == 200 and result.json()["session_id"] == session
        saved = Path("/workspace/.contract-session")
        if saved.exists():
            assert saved.read_text() == session
        else:
            saved.write_text(session)
        # Stop the actual pinned Hermes process, without replacing any runtime service.
        gateways = [
            int(process.name)
            for process in Path("/proc").iterdir()
            if process.name.isdigit()
            and (process / "cmdline").exists()
            and (process / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")[-2:]
            == [b"-m", b"gateway.run"]
        ]
        assert len(gateways) == 1, gateways
        os.kill(gateways[0], signal.SIGSTOP)
        pending = []
        try:
            for _ in range(2):
                opening = operation.model_copy(update={"op_id": uuid4()})
                started = time.monotonic()
                async with response(opening, seam, gateway, inference) as result:
                    assert result.status_code == 200, await result.aread()
                    waiting = result.json()["session_id"]
                assert time.monotonic() - started < 1, "Opening waited for Hermes"
                pending.append(waiting)
                # Let the serial dispatcher enter its blocked native call.
                await asyncio.sleep(0.6)
                opening_state = await seam.get("/session-pending", params={"session_id": waiting})
                assert opening_state.json() == {"pending": True}
            body = {
                "op_id": str(uuid4()),
                "session_id": waiting,
                "message": {"role": "user", "content": "Durable intake fault proof"},
            }
            started = time.monotonic()
            accepted = await seam.post("/message", json=body, timeout=1)
            assert accepted.status_code == 200 and accepted.json()["delivered"] == "queued"
            assert time.monotonic() - started < 1, "Message intake waited for Hermes"
            with sqlite3.connect(
                "file:/workspace/research/.state/store.db?mode=ro", uri=True
            ) as store:
                request, status = store.execute(
                    "SELECT request,status FROM client_admissions WHERE op_id=?", (body["op_id"],)
                ).fetchone()
                assert json.loads(request) == body and status == "queued"
            history = transcript.model_copy(update={"args": {"session_id": waiting}})
            started = time.monotonic()
            async with response(history, seam, gateway, inference) as result:
                assert result.status_code == 200 and result.json()["data"] == []
            assert time.monotonic() - started < 1, "Pending history waited for Hermes"
        finally:
            os.kill(gateways[0], signal.SIGCONT)
        deadline = time.monotonic() + 10
        while pending and time.monotonic() < deadline:
            for waiting in pending[:]:
                ready = await seam.get("/session-pending", params={"session_id": waiting})
                if not ready.json()["pending"]:
                    pending.remove(waiting)
            await asyncio.sleep(0.1)
        assert not pending, "Native openings did not recover after resuming Hermes"
        # This proves intake, not model dispatch: the fixture has no inference lease.
        cancelled = await seam.post("/message-cancel", json={"op_id": body["op_id"]})
        assert cancelled.status_code == 200, cancelled.text
        async with response(history, seam, gateway, inference) as result:
            assert result.status_code == 200 and result.json()["data"] == []
        operation = operation.model_copy(
            update={
                "op_id": uuid4(),
                "args": {
                    **operation.args,
                    "actor": "user:ide-next",
                },
            }
        )
        async with response(operation, seam, gateway, inference) as result:
            assert result.status_code == 422
            assert result.json()["kind"] == "invalid"
        operation = operation.model_copy(
            update={
                "op_id": uuid4(),
                "name": "session.message",
                "args": {
                    "session_id": session,
                    "message": {
                        "role": "user",
                        "content": "contract proof",
                    },
                    "context": {},
                },
            }
        )
        async with response(operation, seam, gateway, inference) as result:
            assert result.status_code == 503
            assert result.json()["kind"] == "inference_unavailable"
        operation = operation.model_copy(
            update={
                "op_id": uuid4(),
                "name": "workspace.snapshot",
                "args": {
                    "store": "workspace",
                },
            }
        )
        async with response(operation, seam, gateway, inference) as result:
            assert result.status_code == 200, await result.aread()
            assert isinstance(result.json()["records"]["sessions"], list)
    print("PASS adapter: durable intake and empty history while native Hermes is stopped;")
    print("  native recovery; open receipt/replay; snapshot; invalid actor; inference gate")


asyncio.run(main())
