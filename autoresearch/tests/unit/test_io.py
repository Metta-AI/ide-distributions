import multiprocessing
import time

import pytest

from autoresearch.io.docs import read_doc, read_json, write_doc, write_json
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.paths import Layout


def test_doc_roundtrip_and_no_temp_file(tmp_path):
    path = tmp_path / "sub" / "c.md"
    write_doc(path, {"id": "C-1", "tags": ["a"]}, "Body text\n")
    meta, body = read_doc(path)
    assert meta == {"id": "C-1", "tags": ["a"]}
    assert body.strip() == "Body text"
    assert [p.name for p in path.parent.iterdir()] == ["c.md"]


def test_json_roundtrip_and_no_temp_file(tmp_path):
    path = tmp_path / "x" / "r.json"
    write_json(path, {"b": 1, "a": [1, 2]})
    assert read_json(path) == {"b": 1, "a": [1, 2]}
    assert [p.name for p in path.parent.iterdir()] == ["r.json"]


def _hold(path, started, release):
    with file_lock(path):
        started.set()
        release.wait(10)


def test_second_lock_holder_times_out(tmp_path):
    lock = tmp_path / "runtime" / "canonical.lock"
    started, release = multiprocessing.Event(), multiprocessing.Event()
    proc = multiprocessing.Process(target=_hold, args=(lock, started, release))
    proc.start()
    try:
        assert started.wait(10)
        t0 = time.monotonic()
        with pytest.raises(LockTimeout), file_lock(lock, timeout_s=0.3):
            pass
        assert time.monotonic() - t0 < 5
    finally:
        release.set()
        proc.join()
    with file_lock(lock, timeout_s=1):
        pass


def test_layout_paths(tmp_path):
    layout = Layout(tmp_path)
    assert layout.claims == tmp_path / "kb/claims"
    assert layout.ledger == tmp_path / ".supervisor/ledger"
    assert layout.plan == tmp_path / "plan.md"
