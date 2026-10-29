"""The Coworld registry in the workspace store (design B.14, amendment A2.5)."""

from __future__ import annotations

import pytest

from research_core.store import coworlds
from research_core.store.errors import NotFound, StoreError

START = "2026-10-28T21:04:05.123456Z"


def test_register_twice_is_a_no_op(workspace_store, harness):
    first = coworlds.register(workspace_store, harness, "cow-x", folder="cow_x")
    assert first == {
        "coworld_id": "cow-x",
        "folder": "cow_x",
        "mixin_version": None,
        "tool_check_status": None,
        "set_up_at": None,
        "active": False,
        "created": START,
        "pending_request": None,
        "existing": False,
    }
    second = coworlds.register(workspace_store, harness, "cow-x", folder="elsewhere")
    assert second["existing"] is True
    assert second["folder"] == "cow_x"
    events = workspace_store.events_after(0)
    assert [(e.kind, e.subject) for e in events] == [("coworld.registered", "cow-x")]
    assert events[0].payload == {"folder": "cow_x"}
    assert [c["coworld_id"] for c in coworlds.list(workspace_store)] == ["cow-x"]
    assert workspace_store.unmarked_changes() == []


def test_register_reports_every_problem_at_once(workspace_store, harness):
    with pytest.raises(StoreError) as info:
        coworlds.register(workspace_store, harness, "", folder=" ")
    assert len(info.value.problems) == 2, info.value.problems
    coworlds.register(workspace_store, harness, "cow-x", folder="cow_x")
    with pytest.raises(StoreError) as info:
        coworlds.register(workspace_store, harness, "cow-y", folder="cow_x")
    assert info.value.problems == ["folder cow_x is registered to cow-x"]
    assert workspace_store.last_seq() == 1


def test_activate_flips_the_single_active_row(workspace_store, harness):
    coworlds.register(workspace_store, harness, "cow-x", folder="cow_x")
    coworlds.register(workspace_store, harness, "cow-y", folder="cow_y")
    assert coworlds.active(workspace_store) is None

    activated = coworlds.activate(workspace_store, harness, "cow-x")
    assert activated["active"] is True
    assert coworlds.active(workspace_store)["coworld_id"] == "cow-x"
    event = workspace_store.events_after(2)[0]
    assert event.kind == "coworld.switched" and event.subject == "cow-x"
    assert event.payload == {"from": None, "to": "cow-x"}

    activated = coworlds.activate(workspace_store, harness, "cow-y")
    assert activated["active"] is True
    assert coworlds.active(workspace_store)["coworld_id"] == "cow-y"
    assert coworlds.read(workspace_store, "cow-x")["active"] is False
    assert [c["active"] for c in coworlds.list(workspace_store)] == [False, True]
    assert workspace_store.events_after(3)[0].payload == {"from": "cow-x", "to": "cow-y"}
    assert workspace_store.one("SELECT COUNT(*) FROM coworlds WHERE active = 1")[0] == 1

    # Activating the active Coworld changes nothing and emits nothing.
    assert coworlds.activate(workspace_store, harness, "cow-y")["active"] is True
    assert workspace_store.last_seq() == 4
    with pytest.raises(NotFound):
        coworlds.activate(workspace_store, harness, "cow-z")


def test_mark_set_up_records_the_mixin_and_the_tool_checks(workspace_store, harness, clock):
    coworlds.register(workspace_store, harness, "cow-x", folder="cow_x")
    clock.advance(30)
    marked = coworlds.mark_set_up(
        workspace_store, harness, "cow-x", mixin_version="1.2.0", tool_check_status="ok"
    )
    assert marked["set_up_at"] == "2026-10-28T21:04:35.123456Z"
    assert marked["mixin_version"] == "1.2.0"
    assert marked["tool_check_status"] == "ok"
    assert marked["created"] == START
    event = workspace_store.events_after(1)[0]
    assert event.kind == "coworld.set_up" and event.subject == "cow-x"
    assert event.payload == {"mixin_version": "1.2.0", "tool_check_status": "ok"}
    with pytest.raises(StoreError) as info:
        coworlds.mark_set_up(
            workspace_store, harness, "cow-x", mixin_version="", tool_check_status=""
        )
    assert len(info.value.problems) == 2
    with pytest.raises(NotFound):
        coworlds.mark_set_up(
            workspace_store, harness, "cow-z", mixin_version="1", tool_check_status="ok"
        )


def test_set_pending_request_round_trip(workspace_store, harness):
    coworlds.register(workspace_store, harness, "cow-x", folder="cow_x")
    coworlds.activate(workspace_store, harness, "cow-x")
    request = {"text": "submit the starter as my champion", "cites": "session:api_1/msg_1"}
    record = coworlds.set_pending_request(workspace_store, harness, "cow-x", request)
    assert record["pending_request"] == request
    assert coworlds.read(workspace_store, "cow-x")["pending_request"] == request
    assert coworlds.active(workspace_store)["pending_request"] == request
    assert coworlds.list(workspace_store)[0]["pending_request"] == request
    # Until the registry row has its pending_request column, the request lives in meta.
    assert workspace_store.get_meta("pending_request:cow-x") is not None
    cleared = coworlds.set_pending_request(workspace_store, harness, "cow-x", None)
    assert cleared["pending_request"] is None
    assert workspace_store.get_meta("pending_request:cow-x") is None
    assert coworlds.read(workspace_store, "cow-x")["pending_request"] is None
    with pytest.raises(StoreError):
        coworlds.set_pending_request(workspace_store, harness, "cow-x", "submit it")
    with pytest.raises(NotFound):
        coworlds.set_pending_request(workspace_store, harness, "cow-z", request)
    assert workspace_store.unmarked_changes() == []


def test_read_and_list(workspace_store, harness, clock):
    assert coworlds.list(workspace_store) == []
    coworlds.register(workspace_store, harness, "cow-b", folder="cow_b")
    clock.advance(1)
    coworlds.register(workspace_store, harness, "cow-a", folder="cow_a")
    assert [c["coworld_id"] for c in coworlds.list(workspace_store)] == ["cow-b", "cow-a"]
    assert coworlds.read(workspace_store, "cow-a")["folder"] == "cow_a"
    assert "existing" not in coworlds.read(workspace_store, "cow-a")
    with pytest.raises(NotFound) as info:
        coworlds.read(workspace_store, "cow-z")
    assert info.value.problems == ["Coworld cow-z is not registered"]
