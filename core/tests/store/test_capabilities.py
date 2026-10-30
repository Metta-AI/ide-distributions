"""Capabilities: marks with transition events, the maintenance Task link and report_failure."""

from __future__ import annotations

import sys

import pytest

from research_core.store import capabilities
from research_core.store.errors import NotFound, StoreError

T0 = "2026-10-28T21:04:05.123456Z"


def kinds(store) -> list[str]:
    return [e.kind for e in store.events_after(0)]


def insert_task(store, actor, capability: str, status: str = "unclaimed") -> int:
    """A raw maintenance Task row (tasks.py is a separate module; this test needs only a row)."""
    with store.tx(actor) as tx:
        cursor = tx.execute(
            "INSERT INTO tasks(type, title, description, status, priority, capability,"
            " created, created_by, updated)"
            " VALUES ('maintenance', 'Repair', '', ?, 'high', ?, ?, ?, ?)",
            (status, capability, tx.at, str(actor), tx.at),
        )
        return cursor.lastrowid


def test_mark_creates_the_row_and_records_the_check(coworld_store, harness):
    record = capabilities.mark(
        coworld_store,
        harness,
        "build",
        status="ok",
        output="built fine",
        command="mixin build",
        kind="mixin command",
    )
    assert record == {
        "name": "build",
        "kind": "mixin command",
        "status": "ok",
        "last_check": T0,
        "output": "built fine",
        "maintenance_task_id": None,
        "command": "mixin build",
    }
    # unknown -> ok is not a failure or a repair: no event.
    assert kinds(coworld_store) == []
    assert coworld_store.unmarked_changes() == []


def test_mark_keeps_kind_and_command_when_not_given(coworld_store, harness, clock):
    capabilities.mark(
        coworld_store, harness, "build", status="ok", output="", command="mixin build", kind="cmd"
    )
    clock.advance(60)
    record = capabilities.mark(coworld_store, harness, "build", status="ok", output="still fine")
    assert record["command"] == "mixin build"
    assert record["kind"] == "cmd"
    assert record["last_check"] == "2026-10-28T21:05:05.123456Z"
    assert record["output"] == "still fine"


def test_transitions_emit_failed_and_repaired(coworld_store, harness):
    capabilities.mark(coworld_store, harness, "build", status="ok", output="")
    capabilities.mark(coworld_store, harness, "build", status="broken", output="exit 1")
    # A repeat failure joins the first: no second event.
    capabilities.mark(coworld_store, harness, "build", status="broken", output="exit 1 again")
    capabilities.mark(coworld_store, harness, "build", status="ok", output="fixed", command="b")
    capabilities.mark(coworld_store, harness, "build", status="ok", output="fine")
    events = coworld_store.events_after(0)
    assert [(e.kind, e.subject) for e in events] == [
        ("capability.failed", "build"),
        ("capability.repaired", "build"),
    ]
    assert events[0].payload == {
        "name": "build",
        "from": "ok",
        "to": "broken",
        "command": None,
        "output_tail": "exit 1",
    }
    assert events[1].payload == {
        "name": "build",
        "from": "broken",
        "to": "ok",
        "command": "b",
        "output_tail": "fixed",
    }


def test_first_failure_of_an_unchecked_capability_is_a_transition(coworld_store, harness):
    capabilities.mark(coworld_store, harness, "upload", status="broken", output="401")
    assert kinds(coworld_store) == ["capability.failed"]
    assert coworld_store.events_after(0)[0].payload["from"] is None
    capabilities.mark(coworld_store, harness, "upload", status="unknown", output="not checked")
    capabilities.mark(coworld_store, harness, "upload", status="broken", output="401")
    assert kinds(coworld_store) == ["capability.failed", "capability.failed"]
    assert coworld_store.events_after(1)[0].payload["from"] == "unknown"


def test_event_output_tail_is_bounded(coworld_store, harness):
    capabilities.mark(coworld_store, harness, "build", status="broken", output="x" * 5000)
    tail = coworld_store.events_after(0)[0].payload["output_tail"]
    assert len(tail) == capabilities.OUTPUT_TAIL_CHARS
    assert capabilities.read(coworld_store, "build")["output"] == "x" * 5000


def test_mark_validates_everything_at_once(coworld_store, harness):
    with pytest.raises(StoreError) as info:
        capabilities.mark(coworld_store, harness, "", status="great", output=None)
    problems = info.value.problems
    assert len(problems) == 3
    assert any("name" in p for p in problems)
    assert any("status" in p for p in problems)
    assert any("output" in p for p in problems)
    assert capabilities.status(coworld_store) == []


def test_status_broken_and_read(coworld_store, harness):
    capabilities.mark(coworld_store, harness, "upload", status="broken", output="401")
    capabilities.mark(coworld_store, harness, "build", status="ok", output="")
    capabilities.mark(coworld_store, harness, "check", status="unknown", output="")
    assert [c["name"] for c in capabilities.status(coworld_store)] == ["build", "check", "upload"]
    assert capabilities.broken(coworld_store) == ["upload"]
    assert capabilities.read(coworld_store, "build")["status"] == "ok"
    assert capabilities.read(coworld_store, "nope") is None


def test_set_maintenance_task_needs_both_rows(coworld_store, harness):
    with pytest.raises(NotFound):
        capabilities.set_maintenance_task(coworld_store, harness, "build", 1)
    capabilities.mark(coworld_store, harness, "build", status="broken", output="x")
    with pytest.raises(NotFound) as info:
        capabilities.set_maintenance_task(coworld_store, harness, "build", 1)
    assert info.value.problems == ["T-1 does not exist"]
    task_id = insert_task(coworld_store, harness, "build")
    record = capabilities.set_maintenance_task(coworld_store, harness, "build", task_id)
    assert record["maintenance_task_id"] == task_id
    cleared = capabilities.set_maintenance_task(coworld_store, harness, "build", None)
    assert cleared["maintenance_task_id"] is None
    # Linking is bookkeeping: no event of its own.
    assert kinds(coworld_store) == ["capability.failed"]


def test_report_failure_without_the_tasks_module_still_marks(coworld_store, harness, monkeypatch):
    monkeypatch.setitem(sys.modules, "research_core.store.tasks", None)
    result = capabilities.report_failure(
        coworld_store, harness, "build", output="boom", command="mixin build"
    )
    assert result["status"] == "broken"
    assert result["output"] == "boom"
    assert result["command"] == "mixin build"
    assert result["maintenance_task"] is None
    assert result["maintenance_task_created"] is False
    assert kinds(coworld_store) == ["capability.failed"]


def test_report_failure_creates_one_repair_task_and_repeats_join_it(coworld_store, harness):
    tasks = pytest.importorskip("research_core.store.tasks")
    first = capabilities.report_failure(
        coworld_store, harness, "build", output="boom", command="mixin build", kind="cmd"
    )
    assert first["status"] == "broken"
    assert first["maintenance_task_created"] is True
    task = first["maintenance_task"]
    assert task["type"] == "maintenance"
    assert task["priority"] == "high"
    assert task["capability"] == "build"
    assert task["goal_id"] is None
    assert task["status"] == "unclaimed"
    assert "mixin build" in task["description"]
    assert first["maintenance_task_id"] == 1

    second = capabilities.report_failure(
        coworld_store, harness, "build", output="boom again", command="mixin build"
    )
    assert second["maintenance_task_created"] is False
    assert second["maintenance_task"]["id"] == task["id"]
    assert second["maintenance_task_id"] == 1
    assert len(tasks.list(coworld_store, type="maintenance")) == 1
    assert kinds(coworld_store).count("capability.failed") == 1


def test_report_failure_opens_a_new_task_once_the_old_repair_is_closed(coworld_store, harness):
    pytest.importorskip("research_core.store.tasks")
    first = capabilities.report_failure(coworld_store, harness, "build", output="boom", command="b")
    with coworld_store.tx(harness) as tx:
        tx.execute(
            "UPDATE tasks SET status = 'complete', outcome = 'succeeded' WHERE id = ?",
            (first["maintenance_task_id"],),
        )
    capabilities.mark(coworld_store, harness, "build", status="ok", output="fixed")
    again = capabilities.report_failure(
        coworld_store, harness, "build", output="boom 2", command="b"
    )
    assert again["maintenance_task_created"] is True
    assert again["maintenance_task_id"] == 2
    assert kinds(coworld_store).count("capability.failed") == 2


def test_report_failure_joins_a_repair_task_it_did_not_link(coworld_store, harness):
    """An open maintenance Task for the capability counts even without the row's link."""
    pytest.importorskip("research_core.store.tasks")
    task_id = insert_task(coworld_store, harness, "build")
    result = capabilities.report_failure(
        coworld_store, harness, "build", output="boom", command="b"
    )
    assert result["maintenance_task_created"] is False
    assert result["maintenance_task_id"] == task_id
