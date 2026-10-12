"""KanbanQueue against a fake ``hermes`` executable on PATH.

The fake records every argv (and the contents of any ``--body-file``) as a JSON line in
``$FAKE_HERMES_LOG`` and replies from ``$FAKE_HERMES_REPLIES``: a JSON object mapping the
Kanban subcommand to ``{"stdout": ..., "code": ..., "stderr": ...}``. JSON shapes are copied
from the real Hermes v0.21.5 output recorded in docs/plans/findings/hermes-facts.md.
"""

import json
import sqlite3
import stat
from dataclasses import asdict

import pytest

from autoresearch.queue.card import CardSpec
from autoresearch.queue.kanban_queue import (
    FOREIGN_CREATOR,
    ForeignCardError,
    KanbanError,
    KanbanQueue,
)

FAKE_HERMES = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
entry = {"argv": argv}
if "--body-file" in argv:
    entry["body_file"] = open(argv[argv.index("--body-file") + 1]).read()
with open(os.environ["FAKE_HERMES_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\\n")
replies = json.load(open(os.environ["FAKE_HERMES_REPLIES"]))
kanban_args = argv[argv.index("kanban") + 1:]
if kanban_args[0] == "--board":
    kanban_args = kanban_args[2:]
reply = replies.get(kanban_args[0], {})
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("code", 0))
"""

SPEC = CardSpec("Q-1", "Probe", "investigation", "because", ["a"], "tester", "P2", 3)


def task_json(status="ready", assignee=None, spec=SPEC, task_id="t_abc"):
    return {
        "id": task_id,
        "title": spec.title,
        "body": json.dumps(asdict(spec)),
        "assignee": assignee,
        "status": status,
        "priority": 33,
    }


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "hermes"
    script.write_text(FAKE_HERMES)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.jsonl"
    replies = tmp_path / "replies.json"
    replies.write_text("{}")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_HERMES_LOG", str(log))
    monkeypatch.setenv("FAKE_HERMES_REPLIES", str(replies))

    class Fake:
        def reply(self, command, stdout="", code=0, stderr=""):
            data = json.loads(replies.read_text())
            data[command] = {
                "stdout": stdout if isinstance(stdout, str) else json.dumps(stdout),
                "code": code,
                "stderr": stderr,
            }
            replies.write_text(json.dumps(data))

        def calls(self):
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

        def argvs(self):
            return [call["argv"] for call in self.calls()]

    return Fake()


@pytest.fixture
def queue():
    return KanbanQueue("board1", "prof")


BOARD = ["kanban", "--board", "board1"]


def test_ensure_board(fake, queue):
    queue.ensure_board()
    assert fake.argvs() == [["kanban", "boards", "create", "board1"]]


def test_board_exists(fake, queue):
    fake.reply("list", [])
    assert queue.board_exists() is True
    fake.reply("list", code=1, stderr="kanban: board 'board1' does not exist.")
    assert queue.board_exists() is False
    fake.reply("list", code=1, stderr="database is locked")
    with pytest.raises(KanbanError):
        queue.board_exists()


def test_create_unassigned_with_an_epoch_key(fake, queue, tmp_path):
    fake.reply("create", {"id": "t_new", "status": "ready", "assignee": None})
    queue.create_unassigned(SPEC, "card/Q-1", tmp_path / "wt", idempotency_key="req:Q-1:e2")
    argv = fake.argvs()[-1]
    assert argv[argv.index("--idempotency-key") + 1] == "req:Q-1:e2"


def test_create_unassigned_argv_and_body(fake, queue, tmp_path):
    fake.reply("create", {"id": "t_abc", "status": "ready", "assignee": None})
    task_id = queue.create_unassigned(SPEC, "card/Q-1", tmp_path / "wt")
    assert task_id == "t_abc"
    (call,) = fake.calls()
    argv = call["argv"]
    assert argv[:3] == BOARD
    assert argv[3:5] == ["create", "Probe"]
    assert argv[argv.index("--branch") + 1] == "card/Q-1"
    assert argv[argv.index("--workspace") + 1] == f"worktree:{tmp_path / 'wt'}"
    assert argv[argv.index("--priority") + 1] == "33"
    assert argv[argv.index("--idempotency-key") + 1] == "req:Q-1"
    assert argv[-1] == "--json"
    assert "--assignee" not in argv
    assert json.loads(call["body_file"]) == asdict(SPEC)


def test_assign_and_unassign(fake, queue):
    queue.assign("t_abc", "prof")
    queue.assign("t_abc", None)
    assert fake.argvs() == [
        [*BOARD, "assign", "t_abc", "prof"],
        [*BOARD, "assign", "t_abc", "none"],
    ]


def test_get_exposes_the_block_reason_of_a_blocked_card(fake, queue):
    runs = [
        {"id": 1, "outcome": "reclaimed", "ended_at": 5, "summary": None},
        {"id": 2, "outcome": "blocked", "ended_at": 9, "summary": "tool-fault: build crashed"},
    ]
    fake.reply("show", {"task": task_json("blocked"), "runs": runs})
    card = queue.get("t_abc")
    assert (card.block_reason, card.block_run_id) == ("tool-fault: build crashed", "2")


def test_an_older_block_reason_is_not_current_after_a_later_run(fake, queue):
    runs = [
        {"id": 1, "outcome": "blocked", "ended_at": 5, "summary": "tool-fault: old"},
        {"id": 2, "outcome": "crashed", "ended_at": 9, "summary": None},
    ]
    fake.reply("show", {"task": task_json("blocked"), "runs": runs})
    card = queue.get("t_abc")
    assert (card.block_reason, card.block_run_id) == (None, None)


def test_block_reason_is_none_without_a_blocked_run_or_a_blocked_card(fake, queue):
    fake.reply("show", {"task": task_json("blocked"), "runs": []})  # e.g. a circuit-breaker park
    assert queue.get("t_abc").block_reason is None
    run = {"id": 1, "outcome": "blocked", "ended_at": 9, "summary": "tool-fault: old"}
    fake.reply("show", {"task": task_json("ready"), "runs": [run]})
    assert queue.get("t_abc").block_reason is None


def test_get_parses_task_and_runs(fake, queue):
    show = {
        "task": task_json("running", "prof"),
        "runs": [
            {"id": 1, "status": "reclaimed", "outcome": "reclaimed", "ended_at": 5},
            {"id": 2, "status": "running", "outcome": None, "ended_at": None},
        ],
    }
    fake.reply("show", show)
    card = queue.get("t_abc")
    assert fake.argvs() == [[*BOARD, "show", "t_abc", "--json"]]
    assert card.task_id == "t_abc" and card.spec == SPEC
    assert card.state == "claimed" and card.assignee == "prof"
    assert [(r.run_id, r.terminal, r.outcome) for r in card.runs] == [
        ("1", True, "reclaimed"),
        ("2", False, None),
    ]


def test_get_reads_the_current_run_and_its_latest_heartbeat(fake, queue):
    """Hermes' JSON has no heartbeat column; the ``heartbeat`` events of the run in flight
    carry it (``heartbeat_worker`` writes both in one transaction)."""
    show = {
        "task": task_json("running", "prof"),
        "runs": [
            {"id": 1, "outcome": "crashed", "ended_at": 5},
            {"id": 2, "outcome": None, "ended_at": None},
        ],
        "events": [
            {"kind": "heartbeat", "created_at": 4, "run_id": 1},
            {"kind": "claimed", "created_at": 6, "run_id": 2},
            {"kind": "heartbeat", "created_at": 70, "run_id": 2},
            {"kind": "heartbeat", "created_at": 130, "run_id": 2},
            {"kind": "commented", "created_at": 140, "run_id": None},
        ],
    }
    fake.reply("show", show)
    card = queue.get("t_abc")
    assert (card.current_run_id, card.last_heartbeat_at) == ("2", 130)


def test_no_current_run_means_no_heartbeat(fake, queue):
    show = {
        "task": task_json("done"),
        "runs": [{"id": 1, "outcome": "completed", "ended_at": 9}],
        "events": [{"kind": "heartbeat", "created_at": 4, "run_id": 1}],
    }
    fake.reply("show", show)
    card = queue.get("t_abc")
    assert (card.current_run_id, card.last_heartbeat_at) == (None, None)
    fake.reply("show", {"task": task_json("running"), "runs": [{"id": 3, "ended_at": None,
                                                                "outcome": None}]})
    card = queue.get("t_abc")
    assert (card.current_run_id, card.last_heartbeat_at) == ("3", None)  # no heartbeat yet


@pytest.mark.parametrize(
    "status,state",
    [
        ("triage", "pending"),
        ("todo", "pending"),
        ("scheduled", "pending"),
        ("ready", "pending"),
        ("running", "claimed"),
        ("review", "claimed"),
        ("blocked", "blocked"),
        ("done", "completed"),
        ("archived", "completed"),
    ],
)
def test_state_mapping(fake, queue, status, state):
    fake.reply("show", {"task": task_json(status), "runs": []})
    assert queue.get("t_abc").state == state


def test_list_filters_by_state_and_returns_runs(fake, queue):
    fake.reply(
        "list",
        [
            task_json("ready", task_id="t_1"),
            task_json("done", task_id="t_2"),
            task_json("blocked", task_id="t_3"),
        ],
    )
    fake.reply(
        "show",
        {
            "task": task_json("done", task_id="t_2"),
            "runs": [{"id": 4, "outcome": "completed", "ended_at": 9}],
        },
    )
    assert [c.task_id for c in queue.list("completed")] == ["t_2"]
    assert [(r.run_id, r.terminal) for r in queue.list("completed")[0].runs] == [("4", True)]
    assert [c.task_id for c in queue.list()] == ["t_2"] * 3  # fake show always returns t_2
    assert fake.argvs()[0] == [*BOARD, "list", "--json"]


def test_a_run_carries_the_worker_hermes_still_records(fake, tmp_path, monkeypatch):
    """Hermes keeps ``worker_pid`` on an ended run until the process is gone. Its show JSON
    has no ``worker_started_at``, so that comes from the board DB, only when it is needed."""
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    home = tmp_path / "hermes-home"
    db_path = home / "kanban" / "boards" / "board1" / "kanban.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE task_runs (id INTEGER, task_id TEXT, worker_started_at TEXT)")
    db.execute("INSERT INTO task_runs VALUES (1, 't_abc', NULL), (2, 't_abc', 'boot|77')")
    db.commit()
    db.close()
    queue = KanbanQueue("board1", "prof", hermes_home=home)
    runs = [
        {"id": 1, "outcome": "blocked", "ended_at": 9, "worker_pid": 4141},
        {"id": 2, "outcome": "blocked", "ended_at": 12, "worker_pid": 4242},
    ]
    fake.reply("show", {"task": task_json("blocked"), "runs": runs})
    found = [(r.worker_pid, r.worker_started_at, r.ended_at) for r in queue.get("t_abc").runs]
    assert found == [(4141, None, 9), (4242, "boot|77", 12)]

    open_run = [{"id": 3, "outcome": None, "ended_at": None, "worker_pid": 4343}]
    fake.reply("show", {"task": task_json("running"), "runs": open_run})
    db_path.unlink()  # not read for a run in flight
    assert queue.get("t_abc").runs[0].worker_pid == 4343


def test_dropped_outcome_comes_from_run_metadata(fake, queue):
    run = {"id": 1, "outcome": "completed", "ended_at": 9, "metadata": {"outcome": "dropped"}}
    fake.reply("show", {"task": task_json("done"), "runs": [run]})
    assert queue.get("t_abc").runs[0].outcome == "dropped"


def test_foreign_body_skipped_in_list_and_error_in_get(fake, queue):
    foreign = task_json("ready", task_id="t_foreign") | {"body": "just some notes"}
    empty = task_json("ready", task_id="t_empty") | {"body": None}
    fake.reply("list", [foreign, empty, task_json("ready", task_id="t_abc")])
    fake.reply("show", {"task": task_json("ready"), "runs": []})
    assert [c.task_id for c in queue.list()] == ["t_abc"]
    fake.reply("show", {"task": foreign, "runs": []})
    with pytest.raises(ForeignCardError, match="t_foreign") as raised:
        queue.get("t_foreign")
    assert (raised.value.task_id, raised.value.title) == ("t_foreign", "Probe")


def test_foreign_cards_are_the_tasks_list_skips_with_a_stand_in_spec(fake, queue):
    foreign = task_json("running", task_id="t_foreign") | {"body": "just some notes"}
    fake.reply("list", [foreign, task_json("ready", task_id="t_abc")])
    run = {"id": 7, "outcome": None, "ended_at": None, "metadata": None}
    fake.reply("show", {"task": foreign, "runs": [run]})

    [card] = queue.foreign_cards()

    assert (card.task_id, card.state, card.board_status) == ("t_foreign", "claimed", "running")
    assert card.spec.title == "Probe" and card.spec.request_id == ""
    assert card.spec.created_by == FOREIGN_CREATOR
    assert card.current_run_id == "7"


def test_an_unknown_hermes_status_reads_as_claimed_and_keeps_the_raw_status(fake, queue):
    fake.reply("list", [task_json("parked")])
    fake.reply("show", {"task": task_json("parked"), "runs": []})

    [card] = queue.list("claimed")

    assert (card.state, card.board_status) == ("claimed", "parked")
    assert queue.list("pending") == []


def test_reprioritize_pending(fake, queue):
    fake.reply("show", {"task": task_json("ready"), "runs": []})
    queue.reprioritize("t_abc", "P0", 2)
    edit = fake.calls()[-1]["argv"]
    assert edit[:4] == [*BOARD, "edit"]
    assert edit[4] == "t_abc"
    assert edit[edit.index("--priority") + 1] == "52"
    body = json.loads(edit[edit.index("--body") + 1])
    assert body["priority"] == "P0" and body["order"] == 2


def test_reprioritize_rejects_non_pending_and_bad_order(fake, queue):
    fake.reply("show", {"task": task_json("running"), "runs": []})
    with pytest.raises(ValueError):
        queue.reprioritize("t_abc", "P0")
    with pytest.raises(ValueError):
        queue.reprioritize("t_abc", "P0", 10)
    assert all(argv[3] != "edit" for argv in fake.argvs())


def test_drop_pending(fake, queue):
    fake.reply("show", {"task": task_json("ready"), "runs": []})
    queue.drop("t_abc", "obsolete")
    assert fake.argvs()[-1] == [
        *BOARD, "complete", "t_abc", "--summary", "dropped: obsolete",
        "--metadata", '{"outcome":"dropped"}',
    ]  # fmt: skip


@pytest.mark.parametrize("status", ["todo", "triage", "scheduled"])
def test_drop_archives_a_pending_card_hermes_will_not_complete(fake, queue, status):
    """Hermes completes only ready (or running, blocked, review) cards, and never a child
    while a parent is unfinished (the box: a todo child of a blocked parent). Archive takes
    any status and the dispatcher never claims these, so the drop archives them."""
    fake.reply("show", {"task": task_json(status), "runs": []})
    queue.drop("t_abc", "obsolete")
    assert fake.argvs()[-2:] == [
        [*BOARD, "archive", "t_abc"],
        [*BOARD, "comment", "t_abc", "dropped: obsolete"],  # after: a retry does not repeat it
    ]
    assert all(argv[3] != "complete" for argv in fake.argvs())


def test_archived_task_ids_come_from_the_list_with_archived_tasks(fake, queue):
    fake.reply("list", [task_json("archived", task_id="t_gone"), task_json("ready")])
    assert queue.archived_task_ids() == {"t_gone"}
    assert fake.argvs()[-1] == [*BOARD, "list", "--archived", "--json"]


def test_an_archived_card_reads_as_completed(fake, queue):
    fake.reply("show", {"task": task_json("archived"), "runs": []})
    card = queue.get("t_abc")
    assert (card.state, card.board_status) == ("completed", "archived")


def test_drop_non_pending_raises(fake, queue):
    fake.reply("show", {"task": task_json("done"), "runs": []})
    with pytest.raises(ValueError):
        queue.drop("t_abc", "x")
    assert all(argv[3] != "complete" for argv in fake.argvs())


def test_requeue_blocked_and_running(fake, queue):
    fake.reply("show", {"task": task_json("blocked"), "runs": []})
    queue.requeue("t_abc", "retry")
    assert fake.argvs()[-1] == [*BOARD, "unblock", "t_abc", "--reason", "retry"]
    fake.reply("show", {"task": task_json("running"), "runs": []})
    queue.requeue("t_abc", "stuck")
    assert fake.argvs()[-1] == [*BOARD, "reclaim", "t_abc"]


def test_requeue_other_states_raise(fake, queue):
    for status in ("ready", "done"):
        fake.reply("show", {"task": task_json(status), "runs": []})
        with pytest.raises(ValueError):
            queue.requeue("t_abc", "x")


def test_nonzero_exit_raises_kanban_error(fake, queue):
    fake.reply("assign", code=1, stderr="boom")
    with pytest.raises(KanbanError) as info:
        queue.assign("t_abc", "prof")
    assert info.value.stderr == "boom" and "assign" in info.value.argv


def test_missing_binary_raises_kanban_error(tmp_path):
    queue = KanbanQueue("b", "p", hermes_bin=str(tmp_path / "nope"))
    with pytest.raises(KanbanError, match="hermes not found"):
        queue.ensure_board()


def test_hermes_home_passed_in_environment(tmp_path):
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs["env"])
        import subprocess

        return subprocess.CompletedProcess(argv, 0, "", "")

    KanbanQueue("b", "p", run=run, hermes_home=tmp_path).ensure_board()
    assert seen["HERMES_HOME"] == str(tmp_path)


# -- read-only commands inside a Hermes delegate_task child --

FENCE_ERROR = "kanban: delegate_task child contexts cannot mutate Kanban tasks or boards"


@pytest.fixture
def fenced(fake, tmp_path, monkeypatch):
    """Hermes refuses list/show (as in a delegate child); the board DB is on disk."""
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    home = tmp_path / "hermes-home"
    db_path = home / "kanban" / "boards" / "board1" / "kanban.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(db_path)
    db.executescript(
        "CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, assignee TEXT, status TEXT,"
        " priority INTEGER);"
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, ended_at INTEGER,"
        " outcome TEXT, metadata TEXT, summary TEXT, last_heartbeat_at INTEGER,"
        " worker_pid INTEGER, worker_started_at TEXT);"
    )
    body = json.dumps(asdict(SPEC))
    db.executemany(
        "INSERT INTO tasks VALUES (?, 'Probe', ?, ?, ?, 33)",
        [
            ("t_ready", body, None, "ready"),
            ("t_run", body, "prof", "running"),
            ("t_gone", body, None, "archived"),
            ("t_foreign", "not json", None, "ready"),
        ],
    )
    db.executemany(
        "INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "t_run", 5, "completed", '{"outcome": "dropped"}', None, 4, None, None),
            (2, "t_run", None, None, None, None, 130, 4242, "boot|77"),
        ],
    )
    db.commit()
    db.close()
    for command in ("list", "show"):
        fake.reply(command, code=1, stderr=FENCE_ERROR)
    return KanbanQueue("board1", "prof", hermes_home=home)


def test_delegate_child_reads_the_block_reason_from_the_board_db(fenced, tmp_path):
    db = sqlite3.connect(tmp_path / "hermes-home/kanban/boards/board1/kanban.db")
    db.execute("UPDATE tasks SET status = 'blocked' WHERE id = 't_ready'")
    db.execute(
        "INSERT INTO task_runs VALUES (5, 't_ready', 9, 'blocked', NULL, 'tool-fault: x', NULL, NULL, NULL)"
    )
    db.commit()
    db.close()
    card = fenced.get("t_ready")
    assert (card.state, card.block_reason, card.block_run_id) == ("blocked", "tool-fault: x", "5")


def test_list_in_delegate_child_reads_the_board_db(fenced):
    cards = {card.task_id: card for card in fenced.list()}
    assert set(cards) == {"t_ready", "t_run"}  # archived hidden, foreign body skipped
    assert cards["t_ready"].state == "pending"
    assert cards["t_run"].assignee == "prof"
    assert [(r.run_id, r.terminal, r.outcome, r.worker_pid) for r in cards["t_run"].runs] == [
        ("1", True, "dropped", None),
        ("2", False, None, 4242),
    ]
    assert fenced.list("pending")[0].task_id == "t_ready"
    # liveness comes from the run in flight: task_runs.last_heartbeat_at
    assert (cards["t_run"].current_run_id, cards["t_run"].last_heartbeat_at) == ("2", 130)
    assert (cards["t_ready"].current_run_id, cards["t_ready"].last_heartbeat_at) == (None, None)


def test_get_in_delegate_child_reads_the_board_db(fenced):
    assert fenced.get("t_ready").spec == SPEC
    with pytest.raises(KanbanError, match="no such task"):
        fenced.get("t_missing")
    with pytest.raises(KanbanError, match="no autoresearch card body"):
        fenced.get("t_foreign")


def test_board_exists_in_delegate_child(fenced, tmp_path):
    assert fenced.board_exists()
    other = KanbanQueue("nope", "prof", hermes_home=tmp_path / "hermes-home")
    assert not other.board_exists()


def test_mutations_are_never_routed_around_the_fence(fenced, fake):
    fake.reply("assign", code=1, stderr=FENCE_ERROR)
    with pytest.raises(KanbanError, match="cannot mutate"):
        fenced.assign("t_ready", "prof")


def test_other_list_failures_are_not_swallowed(fake, queue):
    fake.reply("list", code=1, stderr="kanban: database is locked")
    with pytest.raises(KanbanError, match="locked"):
        queue.list()


def test_board_db_path_follows_hermes_resolution(tmp_path, monkeypatch):
    for name in ("HERMES_KANBAN_DB", "HERMES_KANBAN_HOME", "HERMES_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    native = tmp_path / "user" / ".hermes"
    custom = tmp_path / "custom"

    def db_path(board="b1", **env):
        for name, value in env.items():
            monkeypatch.setenv(name, str(value))
        try:
            return KanbanQueue(board, "p")._board_db_path()
        finally:
            for name in env:
                monkeypatch.delenv(name)

    assert db_path() == native / "kanban" / "boards" / "b1" / "kanban.db"
    assert db_path("Default ") == native / "kanban.db"
    assert db_path("B1") == native / "kanban" / "boards" / "b1" / "kanban.db"
    # a profile dir under the native home, or a non-profile dir under it, resolves to the native root
    assert db_path(HERMES_HOME=native / "profiles" / "x") == db_path()
    assert db_path(HERMES_HOME=native / "other") == db_path()
    # outside the native home: <root>/profiles/<name> -> <root>, anything else is the root itself
    assert (
        db_path(HERMES_HOME=custom / "profiles" / "x")
        == custom / "kanban" / "boards" / "b1" / "kanban.db"
    )
    assert (
        db_path(HERMES_HOME=custom / "plain")
        == custom / "plain" / "kanban" / "boards" / "b1" / "kanban.db"
    )
    # HERMES_KANBAN_HOME beats HERMES_HOME; HERMES_KANBAN_DB beats everything
    kanban_home = tmp_path / "kh"
    assert db_path(HERMES_HOME=custom, HERMES_KANBAN_HOME=kanban_home) == (
        kanban_home / "kanban" / "boards" / "b1" / "kanban.db"
    )
    pinned = tmp_path / "pinned.db"
    assert db_path(HERMES_KANBAN_HOME=kanban_home, HERMES_KANBAN_DB=pinned) == pinned


def test_delegate_child_reads_the_pinned_db(fenced, tmp_path, monkeypatch):
    monkeypatch.setenv(
        "HERMES_KANBAN_DB", str(tmp_path / "hermes-home/kanban/boards/board1/kanban.db")
    )
    queue = KanbanQueue("some-other-board", "prof", hermes_home=tmp_path / "unrelated")
    assert {card.task_id for card in queue.list()} == {"t_ready", "t_run"}


def test_malformed_run_metadata_and_corrupt_db_are_tolerated(fenced, tmp_path):
    db = sqlite3.connect(tmp_path / "hermes-home/kanban/boards/board1/kanban.db")
    db.execute(
        "INSERT INTO task_runs VALUES (3, 't_ready', 9, 'completed', 'not json', NULL, NULL, NULL, NULL)"
    )
    db.execute("INSERT INTO task_runs VALUES (4, 't_ready', 9, 'completed', '[1]', NULL, NULL, NULL, NULL)")
    db.commit()
    db.close()
    assert [r.outcome for r in fenced.get("t_ready").runs] == ["completed", "completed"]


def test_unreadable_board_db_raises_kanban_error(fenced, tmp_path):
    (tmp_path / "hermes-home/kanban/boards/board1/kanban.db").write_text("not a database")
    with pytest.raises(KanbanError, match="board DB read failed"):
        fenced.list()


def test_board_db_only_reads_the_db_without_running_hermes(fenced, fake, tmp_path):
    """The plugin reads the board inside the worker process, where Hermes reads fail anyway."""
    queue = KanbanQueue("board1", "prof", hermes_home=tmp_path / "hermes-home", board_db_only=True)
    running = queue.list("claimed")
    assert [(c.task_id, c.current_run_id, c.last_heartbeat_at) for c in running] == [
        ("t_run", "2", 130)
    ]
    with pytest.raises(KanbanError, match="no such task"):
        queue.get("t_missing")
    assert fake.calls() == []


def test_list_skips_a_card_removed_between_list_and_show(fake, queue):
    fake.reply("list", [task_json("running", task_id="t_gone")])
    fake.reply("show", code=1, stderr="no such task: t_gone")
    assert queue.list() == []
    fake.reply("show", code=1, stderr="kanban: database is locked")
    with pytest.raises(KanbanError, match="locked"):
        queue.list()


def test_create_pins_the_model_of_the_card_mode(fake, tmp_path):
    """Mechanical modes run on a cheaper model: Hermes ``kanban create --model --provider``
    sets the task's model override, which the dispatcher passes to the worker as ``-m``."""
    queue = KanbanQueue("board1", "prof", models={"planning": "z-ai/glm-5.3"})
    fake.reply("create", {"id": "t_abc", "status": "ready", "assignee": None})
    planning = CardSpec("Q-2", "Plan", "planning", "because", [], "tester", "P2")
    queue.create_unassigned(planning, "card/Q-2", tmp_path / "wt")
    queue.create_unassigned(SPEC, "card/Q-1", tmp_path / "wt")  # investigation: not in the map
    pinned, default = fake.argvs()
    assert pinned[pinned.index("--model") + 1] == "z-ai/glm-5.3"
    assert pinned[pinned.index("--provider") + 1] == "openrouter"
    assert "--model" not in default and "--provider" not in default


def test_card_models_default_and_override():
    from autoresearch.queue.select import DEFAULT_CARD_MODELS, card_models

    assert card_models({}) == DEFAULT_CARD_MODELS == {
        "planning": "z-ai/glm-5.3",
        "scouting": "z-ai/glm-5.3",
        "maintenance": "z-ai/glm-5.3",
    }
    assert card_models({"AUTORESEARCH_CARD_MODELS": "planning=a/b, scouting=default"}) == {
        "planning": "a/b"
    }
    assert card_models({"AUTORESEARCH_CARD_MODELS": "none"}) == {}


def test_bad_card_model_entries_are_skipped_never_raised():
    """A typo must not stop queue-keeper, health or submit (they all build the queue)."""
    from autoresearch.queue.select import card_models, parse_card_models

    models, problems = parse_card_models("planing=a/b, scouting, maintenance=c/d")
    assert models == {"maintenance": "c/d"}
    assert len(problems) == 2 and "planing" in problems[0] and "scouting" in problems[1]
    assert card_models({"AUTORESEARCH_CARD_MODELS": "planning="}) == {}


def test_queue_from_env_gives_the_kanban_queue_the_model_map(tmp_path):
    from autoresearch.queue.select import queue_from_env
    from autoresearch.runctx import RunContext

    ctx = RunContext(tmp_path / "autoresearch", tmp_path / "ws", None, None, None)
    queue = queue_from_env({"AUTORESEARCH_CARD_MODELS": "planning=x/y"}, ctx)
    assert queue.models == {"planning": "x/y"}


def _shown(status, events=()):
    return {"task": task_json(status=status), "runs": [], "events": list(events)}


def test_stop_blocks_the_task_with_a_typed_kind_and_reports_where_it_landed(fake, queue):
    fake.reply("show", _shown("running"))
    fake.reply("block", "Blocked t_abc")
    assert queue.stop("t_abc", "spend-cap: card used $4.10 of $4.00") == "running"  # fake show
    block = [argv for argv in fake.argvs() if "block" in argv]
    assert block == [[*BOARD, "block", "t_abc", "spend-cap: card used $4.10 of $4.00",
                      "--kind", "capability"]]


def test_a_repeat_stop_alternates_the_kind_so_hermes_keeps_it_blocked(fake, queue):
    """Same-kind block -> unblock -> block goes to triage in Hermes (BLOCK_RECURRENCE_LIMIT)."""
    fake.reply("show", _shown("ready", [
        {"kind": "blocked", "payload": {"kind": "capability"}}, {"kind": "unblocked"},
    ]))
    queue.stop("t_abc", "spend-cap: x")
    [block] = [argv for argv in fake.argvs() if "block" in argv]
    assert block[-2:] == ["--kind", "needs_input"]


@pytest.mark.parametrize("status", ["triage", "todo", "review", "blocked", "scheduled"])
def test_stop_refuses_statuses_hermes_cannot_block(fake, queue, status):
    fake.reply("show", _shown(status))
    with pytest.raises(ValueError):
        queue.stop("t_abc", "spend-cap: x")
    assert not [argv for argv in fake.argvs() if "block" in argv]
