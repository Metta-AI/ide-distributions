"""KanbanQueue against a fake ``hermes`` executable on PATH.

The fake records every argv (and the contents of any ``--body-file``) as a JSON line in
``$FAKE_HERMES_LOG`` and replies from ``$FAKE_HERMES_REPLIES``: a JSON object mapping the
Kanban subcommand to ``{"stdout": ..., "code": ..., "stderr": ...}``. JSON shapes are copied
from the real Hermes v0.21.5 output recorded in docs/plans/findings/hermes-facts.md.
"""

import json
import stat
from dataclasses import asdict

import pytest

from autoresearch.queue.card import CardSpec
from autoresearch.queue.kanban_queue import KanbanError, KanbanQueue

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
    with pytest.raises(KanbanError, match="t_foreign"):
        queue.get("t_foreign")


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
