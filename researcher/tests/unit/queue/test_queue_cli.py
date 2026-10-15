"""Queue CLI: subprocess tests for ``autoresearch queue list|show|request``."""

import json
import os
import stat
import subprocess
import sys

import pytest

from autoresearch.queue.card import CardSpec
from autoresearch.queue.file_queue import FileQueue

CARD_ID = "K-CURRENT"


@pytest.fixture
def worker_env(tmp_path):
    home = tmp_path / "profiles" / "alpha"
    canonical = tmp_path / "canonical"
    worker = tmp_path / "worker"
    for directory in (home, canonical, worker):
        directory.mkdir(parents=True)
    env = {
        **os.environ,
        "AUTORESEARCH_QUEUE": "file",
        "HERMES_HOME": str(home),
        "AUTORESEARCH_WORKSPACE": str(canonical),
        "HERMES_KANBAN_WORKSPACE": str(worker),
        "HERMES_KANBAN_TASK": CARD_ID,
        "HERMES_KANBAN_RUN_ID": "r-1",
    }
    env.pop("AUTORESEARCH_ROOT", None)
    return env, canonical, worker


def run_cli(env, *args):
    result = subprocess.run(
        [sys.executable, "-m", "autoresearch", "queue", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, json.loads(result.stdout)


def seed_queue(canonical):
    queue = FileQueue(canonical / ".supervisor/runtime/file_queue")
    spec = CardSpec("Q-1", "first", "scouting", "because", ["kb:1"], "planner", "P1")
    task_id = queue.create_unassigned(spec, "branch", canonical)
    return queue, task_id


def test_list_and_show(worker_env):
    env, canonical, _ = worker_env
    _, task_id = seed_queue(canonical)
    code, out = run_cli(env, "list")
    assert code == 0 and out["ok"]
    assert [card["task_id"] for card in out["data"]["cards"]] == [task_id]
    listed = out["data"]["cards"][0]
    assert (listed["current_run_id"], listed["last_heartbeat_at"]) == (None, None)
    code, out = run_cli(env, "list", "--state", "claimed")
    assert code == 0 and out["data"]["cards"] == []
    code, out = run_cli(env, "show", task_id)
    assert code == 0 and out["data"]["spec"]["title"] == "first"
    assert out["data"]["state"] == "pending"


def test_show_unknown_card_fails(worker_env):
    env, _, _ = worker_env
    code, out = run_cli(env, "show", "K-NOPE")
    assert code == 1 and not out["ok"]


def test_request_create_writes_valid_file(worker_env):
    env, _, worker = worker_env
    code, out = run_cli(
        env, "request", "create", "--title", "T", "--mode", "scouting", "--why", "W",
        "--ref", "kb:a", "--ref", "kb:b", "--priority", "P2", "--order", "3",
    )  # fmt: skip
    assert code == 0
    request_id = out["data"]["request_id"]
    written = json.loads((worker / "cards/requests" / f"{request_id}.json").read_text())
    assert written["op"] == "create"
    assert written["spec"]["created_by"] == CARD_ID
    assert written["spec"]["refs"] == ["kb:a", "kb:b"]
    assert written["spec"]["order"] == 3


def test_request_reprioritize_and_drop(worker_env):
    env, _, worker = worker_env
    target = "Q-" + "0" * 26
    code, out = run_cli(env, "request", "reprioritize", target, "--priority", "P0", "--reason", "r")
    assert code == 0
    path = worker / "cards/requests" / f"{out['data']['request_id']}.json"
    assert json.loads(path.read_text())["priority"] == "P0"
    code, out = run_cli(env, "request", "drop", target, "--reason", "stale")
    assert code == 0
    path = worker / "cards/requests" / f"{out['data']['request_id']}.json"
    assert json.loads(path.read_text())["op"] == "drop"


def test_request_outside_worker_is_bad_args(worker_env):
    env, _, _ = worker_env
    env = {k: v for k, v in env.items() if k != "HERMES_KANBAN_WORKSPACE"}
    code, out = run_cli(env, "request", "drop", "Q-" + "0" * 26, "--reason", "x")
    assert code == 2 and not out["ok"]


def test_request_invalid_target_id_is_bad_args(worker_env):
    env, _, _ = worker_env
    code, out = run_cli(env, "request", "drop", "not-an-id", "--reason", "x")
    assert code == 2 and not out["ok"]


def test_kanban_backend_uses_fake_hermes(worker_env, tmp_path):
    env, _, _ = worker_env
    env = {k: v for k, v in env.items() if k != "AUTORESEARCH_QUEUE"}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.log"
    fake = bin_dir / "hermes"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\necho "[]"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    code, out = run_cli(env, "list")
    assert code == 0 and out["data"]["cards"] == []
    assert "--board autoresearch" in log.read_text()  # the board slug, not the profile name


def test_kanban_missing_hermes_exits_3(worker_env, tmp_path):
    env, _, _ = worker_env
    env = {k: v for k, v in env.items() if k != "AUTORESEARCH_QUEUE"}
    empty = tmp_path / "empty"
    empty.mkdir()
    env["PATH"] = str(empty)
    code, out = run_cli(env, "list")
    assert code == 3 and out["ok"] is False
