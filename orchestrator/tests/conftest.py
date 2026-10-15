import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from orchestrator.context import Context
from orchestrator.launch import lock_sha


@pytest.fixture
def context(tmp_path):
    home = tmp_path / "host/profiles/orchestrator"
    home.mkdir(parents=True)
    workspace = home / "workspace"
    workspace.mkdir()
    return Context(
        home,
        {
            "PATH": os.environ["PATH"],
            "HERMES_HOME": str(home),
            "RESEARCH_WORKSPACE": str(workspace),
            "RESEARCH_PLAYER_ID": "ply_test",
            "SOFTMAX_PLAYER_CREDENTIAL": "cga_fake",
        },
    )


@pytest.fixture
def board_file(context):
    """Just the native columns the model-free gate reads; native mutations have Hermes tests."""
    path = context.board_path
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, branch_name TEXT, body TEXT);
            CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT);
            CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);
            CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, ended_at INTEGER,
                                    metadata TEXT);
            INSERT INTO tasks VALUES ('card-1', 'done', 'card/test', '');
        """)
    return path


@pytest.fixture
def shared_fixture(context):
    """A real subprocess fixture implementing only the documented CLI envelopes."""
    package = context.home / "fixture/ide_research_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text("""
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with (Path(os.environ['HERMES_HOME']) / 'calls.jsonl').open('a') as output:
    output.write(json.dumps(args) + '\\n')
if 'refuse' in args:
    print(json.dumps({'ok': False, 'error': {'reason': 'baseline_not_champion',
                                         'message': 'baseline changed'}}))
    sys.exit(2)
if args == ['promotion', 'status']:
    data = {'in_flight': None, 'champion': None, 'league_champion': None, 'conflict': None}
elif args == ['records', 'summary']:
    data = {'champion': None}
else:
    data = {'args': args}
print(json.dumps({'ok': True, 'data': data}))
""")
    venv = context.home / "local/venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(Path(sys.executable).resolve())
    (context.home / "uv.lock").write_text("contract fixture\n")
    (venv / ".lock-sha").write_text(lock_sha(context.home))
    context.env["PYTHONPATH"] = str(package.parent)
    return context


def add_wait(path, awaiting, experiment=None):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO task_runs VALUES (1, ?, 1, ?)",
            ("card-1", json.dumps({"awaiting": awaiting, "experiment": experiment})),
        )
