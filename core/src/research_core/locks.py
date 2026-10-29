"""Advisory inter-process file lock on ``fcntl.flock``. Stdlib only."""

from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

POLL_INTERVAL_S = 0.05


class LockTimeout(TimeoutError):
    pass


@contextmanager
def file_lock(path: Path, timeout_s: float = 60) -> Iterator[TextIO]:
    """Hold an exclusive lock on ``path`` for the duration of the block.

    Polls a non-blocking ``flock`` every 50 ms until ``timeout_s`` has passed, then raises
    ``LockTimeout``. Yields the open handle (append mode) so a holder can write who it is into
    the file. The parent directory is created when missing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    with open(path, "a") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"could not acquire {path} within {timeout_s}s") from None
                time.sleep(POLL_INTERVAL_S)
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
