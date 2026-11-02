"""Graceful cleanup must let a real child flush its state before it is reaped."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from image.processes import managed_process


@pytest.mark.parametrize("cancelled", [False, True])
def test_child_flushes_on_supervisor_exit(tmp_path: Path, cancelled: bool):
    child_source = """
import signal, sys
from pathlib import Path
root = Path(sys.argv[1])
def stop(signum, frame):
    (root / 'flushed').write_text('saved')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(root / 'ready').touch()
signal.pause()
"""

    async def exercise():
        async with managed_process(
            sys.executable, "-c", child_source, str(tmp_path), env=dict(os.environ)
        ) as child:
            async with asyncio.timeout(5):
                while not (tmp_path / "ready").exists():
                    await asyncio.sleep(0.01)
            if cancelled:
                raise asyncio.CancelledError
        assert child.returncode == 0

    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert (tmp_path / "flushed").read_text() == "saved"
