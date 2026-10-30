import time

import pytest

from research_core import locks


def test_file_lock_creates_parents_yields_handle_and_releases(tmp_path):
    path = tmp_path / "nested" / "dir" / "a.lock"
    with locks.file_lock(path) as handle:
        assert path.exists()
        handle.write("holder\n")
    # Released: a second acquisition with a short timeout succeeds at once.
    with locks.file_lock(path, timeout_s=0.1):
        pass


def test_locks_timeout(tmp_path):
    path = tmp_path / "a.lock"
    with locks.file_lock(path):
        started = time.monotonic()
        with pytest.raises(locks.LockTimeout) as info:
            with locks.file_lock(path, timeout_s=0.2):
                pass
        assert time.monotonic() - started >= 0.2
    assert isinstance(info.value, TimeoutError)
    assert str(path) in str(info.value)
