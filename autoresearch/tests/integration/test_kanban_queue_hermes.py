"""KanbanQueue against real Hermes in a throwaway HERMES_HOME. Needs no model or API key."""

import shutil

import pytest

from autoresearch.queue.card import CardSpec
from autoresearch.queue.kanban_queue import KanbanQueue

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
