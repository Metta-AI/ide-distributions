"""KanbanQueue against real Hermes in a throwaway HERMES_HOME. Needs no model or API key."""

import os
import shutil
import sqlite3
import subprocess

import pytest

from ide_research_core.queue.card import CardSpec
from ide_research_core.queue.kanban_queue import KanbanQueue

pytestmark = [
    pytest.mark.hermes,
    pytest.mark.skipif(shutil.which("hermes") is None, reason="hermes not installed"),
]

SPEC = CardSpec("Q-it1", "Integration probe", "investigation", "why", ["r"], "tester", "P2", 1)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "kanban-home"))
    queue = KanbanQueue("itboard", "itprofile", hermes_home=tmp_path / "hh")
    queue.ensure_board()
    return queue


def test_operations_against_real_hermes(queue, tmp_path):
    queue.ensure_board()  # idempotent

    task_id = queue.create_unassigned(SPEC, "card/Q-it1", tmp_path / "wt")
    assert queue.create_unassigned(SPEC, "card/Q-it1", tmp_path / "wt") == task_id

    card = queue.get(task_id)
    assert card.spec == SPEC and card.state == "pending" and card.assignee is None
    assert card.runs == []

    queue.assign(task_id, "itprofile")
    assert queue.get(task_id).assignee == "itprofile"
    queue.assign(task_id, None)
    assert queue.get(task_id).assignee is None

    queue.reprioritize(task_id, "P0", 2)
    reprioritized = queue.get(task_id)
    assert (reprioritized.spec.priority, reprioritized.spec.order) == ("P0", 2)

    assert [c.task_id for c in queue.list()] == [task_id]
    assert [c.task_id for c in queue.list("pending")] == [task_id]
    assert queue.list()[0].runs == []

    queue.drop(task_id, "integration test")
    dropped = queue.get(task_id)
    assert dropped.state == "completed"
    assert [(r.terminal, r.outcome) for r in dropped.runs] == [(True, "dropped")]
    assert [r.outcome for r in queue.list("completed")[0].runs] == ["dropped"]
    with pytest.raises(ValueError):
        queue.drop(task_id, "again")


def test_drop_of_a_todo_child_of_a_blocked_parent(queue, tmp_path):
    """The box (2026-10-09): Hermes refuses ``complete`` of a todo child while its parent is
    blocked, so the drop archives it; the blocked parent is left as it is."""
    parent = queue.create_unassigned(SPEC, "card/Q-it1", tmp_path / "wt1")
    child_spec = CardSpec("Q-it2", "Child", "investigation", "why", ["r"], "tester", "P2", 1)
    child = queue.create_unassigned(child_spec, "card/Q-it2", tmp_path / "wt2")
    queue._kanban("link", parent, child)
    queue._kanban("block", parent, "waiting on a human")
    assert queue.get(child).board_status == "todo"

    queue.drop(child, "stale")

    dropped = queue.get(child)
    assert (dropped.state, dropped.board_status) == ("completed", "archived")
    assert queue.get(parent).board_status == "blocked"
    assert [c.task_id for c in queue.list()] == [parent]  # archived cards are hidden
    assert queue.archived_task_ids() == {child}  # rebuild reads them separately


def test_a_stopped_run_keeps_its_worker_until_hermes_clears_it(queue, tmp_path):
    """``kanban block`` ends the run and leaves ``task_runs.worker_pid`` on it: the worker is
    not signalled. ``runtime.blocked`` waits for that field to clear before it opens the
    repair card of a stopped rerun."""
    worktree = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)  # ``claim`` needs a repo
    identity = ["-c", "user.name=T", "-c", "user.email=t@example.com"]
    subprocess.run(
        ["git", "-C", str(worktree), *identity, "commit", "-q", "--allow-empty", "-m", "seed"],
        check=True,
    )
    task_id = queue.create_unassigned(SPEC, "card/Q-it1", worktree)
    queue.assign(task_id, "itprofile")
    queue._kanban("claim", task_id)
    assert queue.get(task_id).board_status == "running"
    db = sqlite3.connect(queue._board_db_path())  # the dispatcher records the pid at spawn
    db.execute("UPDATE task_runs SET worker_pid = ? WHERE task_id = ?", (os.getpid(), task_id))
    db.commit()
    db.close()

    assert queue.stop(task_id, "handoff already published") == "blocked"

    [run] = queue.get(task_id).runs
    assert (run.terminal, run.outcome, run.worker_pid) == (True, "blocked", os.getpid())
    # ``kanban claim`` records no start fingerprint: a row Hermes' reaper never clears.
    assert run.worker_started_at is None and run.ended_at is not None


def test_hermes_makes_a_todo_child_ready_when_its_parent_completes(queue, tmp_path):
    """So a ``todo`` card can start by itself: ``runtime.blocked`` writes nothing for a
    ``todo`` rerun before it is closed."""
    parent = queue.create_unassigned(SPEC, "card/Q-it1", tmp_path / "wt1")
    child_spec = CardSpec("Q-it2", "Child", "investigation", "why", ["r"], "tester", "P2", 1)
    child = queue.create_unassigned(child_spec, "card/Q-it2", tmp_path / "wt2")
    queue._kanban("link", parent, child)
    assert queue.get(child).board_status == "todo"

    queue.drop(parent, "done")

    assert queue.get(child).board_status == "ready"
