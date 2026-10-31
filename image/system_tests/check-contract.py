"""Exercise the packaged public adapter against the running seam, without model credentials."""

import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application import Operation, response  # noqa: E402
from inference import Availability  # noqa: E402

from contract import Request  # noqa: E402


async def main():
    inference = Availability()
    async with (
        httpx.AsyncClient(base_url="http://127.0.0.1:8650") as seam,
        httpx.AsyncClient(base_url="http://127.0.0.1:8642") as gateway,
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
        # Recover the native receipt, including the second retained-volume cycle.
        async with response(operation, seam, gateway, inference) as result:
            assert result.status_code == 200 and result.json()["session_id"] == session
        saved = Path("/workspace/.contract-session")
        if saved.exists():
            assert saved.read_text() == session
        else:
            saved.write_text(session)
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
                    "message": "contract proof",
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
    print("PASS adapter: durable open receipt/replay; snapshot; invalid actor; inference gate")


asyncio.run(main())
