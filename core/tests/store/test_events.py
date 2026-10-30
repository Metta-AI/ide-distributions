"""The Event record and the watcher's handled_events bookkeeping (events stay append-only)."""

from __future__ import annotations

import pytest

from research_core.store import events
from research_core.store.errors import NotFound
from research_core.store.events import Event


def emit(store, actor, *kinds: str) -> list[int]:
    seqs = []
    with store.tx(actor) as tx:
        for kind in kinds:
            seqs.append(tx.event(kind, "T-1", {"kind": kind}))
    return seqs


def test_event_dataclass_round_trip(coworld_store, harness):
    emit(coworld_store, harness, "task.completed")
    event = coworld_store.events_after(0)[0]
    assert event == Event(
        seq=1,
        at="2026-10-28T21:04:05.123456Z",
        actor="harness",
        kind="task.completed",
        subject="T-1",
        payload={"kind": "task.completed"},
    )
    assert event.to_dict() == {
        "seq": 1,
        "at": "2026-10-28T21:04:05.123456Z",
        "actor": "harness",
        "kind": "task.completed",
        "subject": "T-1",
        "payload": {"kind": "task.completed"},
    }
    row = coworld_store.one("SELECT * FROM events WHERE seq = 1")
    assert Event.from_row(row) == event


def test_unhandled_lists_only_the_asked_kinds_in_order(coworld_store, harness):
    emit(coworld_store, harness, "task.completed", "notice", "task.asked", "task.completed")
    pending = events.unhandled(coworld_store, ["task.completed", "task.asked"])
    assert [(e.seq, e.kind) for e in pending] == [
        (1, "task.completed"),
        (3, "task.asked"),
        (4, "task.completed"),
    ]
    assert events.unhandled(coworld_store, []) == []
    assert events.unhandled(coworld_store, ["nothing"]) == []


def test_claim_handling_counts_attempts_and_keeps_the_event_unhandled(coworld_store, harness):
    emit(coworld_store, harness, "task.completed")
    assert events.claim_handling(coworld_store, harness, 1, "task.completed", "api_1_a") == 1
    assert events.claim_handling(coworld_store, harness, 1, "task.completed", "api_1_b") == 2
    row = coworld_store.one("SELECT * FROM handled_events WHERE seq = 1")
    assert row["attempts"] == 2
    assert row["session_id"] == "api_1_b"
    assert row["kind"] == "task.completed"
    assert row["handled_at"] is None
    # Claimed but not handled: still listed, so a crashed handler is retried; attempts counts.
    assert [e.seq for e in events.unhandled(coworld_store, ["task.completed"])] == [1]
    assert coworld_store.unmarked_changes() == []


def test_mark_handled_removes_the_event_from_unhandled(coworld_store, harness):
    emit(coworld_store, harness, "task.completed", "task.completed")
    events.claim_handling(coworld_store, harness, 1, "task.completed", "api_1_a")
    events.mark_handled(coworld_store, harness, 1, "run_1")
    row = coworld_store.one("SELECT * FROM handled_events WHERE seq = 1")
    assert row["run_id"] == "run_1"
    assert row["handled_at"] == "2026-10-28T21:04:05.123456Z"
    assert [e.seq for e in events.unhandled(coworld_store, ["task.completed"])] == [2]
    # Marking without a prior claim creates the row from the event's kind.
    events.mark_handled(coworld_store, harness, 2, None)
    row = coworld_store.one("SELECT * FROM handled_events WHERE seq = 2")
    assert row["kind"] == "task.completed" and row["attempts"] == 0
    assert events.unhandled(coworld_store, ["task.completed"]) == []


def test_mark_failed_records_the_error_and_keeps_it_unhandled(coworld_store, harness):
    emit(coworld_store, harness, "task.completed")
    events.claim_handling(coworld_store, harness, 1, "task.completed", "api_1_a")
    events.mark_failed(coworld_store, harness, 1, "runs API 502")
    row = coworld_store.one("SELECT * FROM handled_events WHERE seq = 1")
    assert row["last_error"] == "runs API 502"
    assert row["handled_at"] is None and row["attempts"] == 1
    assert [e.seq for e in events.unhandled(coworld_store, ["task.completed"])] == [1]
    events.mark_handled(coworld_store, harness, 1, "run_2")
    assert events.unhandled(coworld_store, ["task.completed"]) == []
    assert coworld_store.one("SELECT last_error FROM handled_events WHERE seq = 1")[0] is None


def test_handling_an_unknown_event_is_refused(coworld_store, harness):
    with pytest.raises(NotFound):
        events.mark_handled(coworld_store, harness, 99, "run")
    with pytest.raises(NotFound):
        events.mark_failed(coworld_store, harness, 99, "x")
    with pytest.raises(NotFound):
        events.claim_handling(coworld_store, harness, 99, "task.completed", "s")


def test_handled_events_works_in_the_workspace_store(workspace_store, harness):
    emit(workspace_store, harness, "notice")
    assert events.claim_handling(workspace_store, harness, 1, "notice", "s") == 1
    events.mark_handled(workspace_store, harness, 1, None)
    assert events.unhandled(workspace_store, ["notice"]) == []
