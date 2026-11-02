"""Reap owned subprocesses before the container supervisor exits."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def managed_process(
    *args: str, env: dict[str, str]
) -> AsyncIterator[asyncio.subprocess.Process]:
    child = await asyncio.create_subprocess_exec(*args, env=env)
    try:
        yield child
    finally:
        if child.returncode is None:
            child.terminate()
            finished = asyncio.create_task(child.wait())
            done, _ = await asyncio.wait([finished], timeout=10)
            if not done:
                child.kill()
            await finished
