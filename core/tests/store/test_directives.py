"""Directives: scope validation per store, updates, retirement and listing."""

from __future__ import annotations

import pytest

from research_core.store import directives, goals
from research_core.store.errors import NotFound, StoreError

START = "2026-10-28T21:04:05.123456Z"


def make_goal(store, actor, title="Beat the starter"):
    return goals.create(store, actor, title=title, end_state="A positive margin", reason="asked")


def test_add_a_workspace_directive_in_the_workspace_store(workspace_store, session_actor):
    directive = directives.add(
        workspace_store,
        session_actor,
        text="Never submit on Fridays",
        scope="workspace",
        source="session:api_1/msg_3",
    )
    assert directive == {
        "id": "D-1",
        "text": "Never submit on Fridays",
        "scope": "workspace",
        "source": "session:api_1/msg_3",
        "status": "active",
        "created": START,
        "created_by": str(session_actor),
        "retired": None,
    }
    events = workspace_store.events_after(0)
    assert [(e.kind, e.subject, e.actor) for e in events] == [
        ("directive.added", "D-1", str(session_actor))
    ]
    assert events[0].payload == {"scope": "workspace"}
    assert workspace_store.unmarked_changes() == []


def test_add_a_goal_directive_needs_the_goal(coworld_store, harness):
    with pytest.raises(StoreError) as info:
        directives.add(coworld_store, harness, text="Keep it", scope="goal:99", source="m")
    assert info.value.problems == ["G-99 does not exist"]
    make_goal(coworld_store, harness)
    directive = directives.add(coworld_store, harness, text="Keep it", scope="goal:1", source="m")
    assert directive["id"] == "D-1" and directive["scope"] == "goal:1"
    assert coworld_store.events_after(1)[0].payload == {"scope": "goal:1"}


def test_add_reports_every_problem_at_once(workspace_store, harness):
    with pytest.raises(StoreError) as info:
        directives.add(workspace_store, harness, text=" ", scope="goal:x", source="")
    problems = info.value.problems
    assert len(problems) == 3, problems
    for field in ("text", "scope", "source"):
        assert any(p.startswith(field) for p in problems), (field, problems)
    for bad_scope in ("", "goal:0", "Goal:1", "goal:1 ", "coworld"):
        with pytest.raises(StoreError) as info:
            directives.add(workspace_store, harness, text="t", scope=bad_scope, source="s")
        assert len(info.value.problems) == 1, bad_scope
    assert workspace_store.last_seq() == 0


def test_scope_must_match_the_store(coworld_store, workspace_store, harness):
    make_goal(coworld_store, harness)
    with pytest.raises(StoreError) as info:
        directives.add(coworld_store, harness, text="t", scope="workspace", source="s")
    assert info.value.problems == [
        "scope workspace: workspace directives live in the workspace store"
    ]
    with pytest.raises(StoreError) as info:
        directives.add(workspace_store, harness, text="t", scope="goal:1", source="s")
    assert info.value.problems == ["scope goal:1: Goal directives live in the Coworld store"]


def test_update_changes_text_or_scope(coworld_store, harness, clock):
    make_goal(coworld_store, harness)
    make_goal(coworld_store, harness, title="Other")
    directives.add(coworld_store, harness, text="Keep it", scope="goal:1", source="m")
    clock.advance(60)
    directive = directives.update(coworld_store, harness, 1, text="Keep it always")
    assert directive["text"] == "Keep it always" and directive["scope"] == "goal:1"
    assert directive["created"] == START
    event = coworld_store.events_after(3)[0]
    assert event.kind == "directive.updated" and event.subject == "D-1"
    assert event.payload == {"fields": ["text"]}
    directive = directives.update(coworld_store, harness, 1, scope="goal:2")
    assert directive["scope"] == "goal:2"
    with pytest.raises(StoreError) as info:
        directives.update(coworld_store, harness, 1, text="", scope="goal:9")
    assert info.value.problems == ["text is required", "G-9 does not exist"]
    with pytest.raises(StoreError) as info:
        directives.update(coworld_store, harness, 1)
    assert len(info.value.problems) == 1
    with pytest.raises(NotFound):
        directives.update(coworld_store, harness, 5, text="x")


def test_retire_once(workspace_store, harness, clock):
    directives.add(workspace_store, harness, text="t", scope="workspace", source="s")
    clock.advance(5)
    retired = directives.retire(workspace_store, harness, 1)
    assert retired["status"] == "retired"
    assert retired["retired"] == "2026-10-28T21:04:10.123456Z"
    event = workspace_store.events_after(1)[0]
    assert event.kind == "directive.retired" and event.subject == "D-1"
    with pytest.raises(StoreError) as info:
        directives.retire(workspace_store, harness, 1)
    assert info.value.problems == ["D-1 is already retired"]
    with pytest.raises(StoreError) as info:
        directives.update(workspace_store, harness, 1, text="again")
    assert info.value.problems == ["D-1 is retired; add a new directive instead"]
    assert workspace_store.last_seq() == 2
    with pytest.raises(NotFound):
        directives.retire(workspace_store, harness, 2)


def test_list_filters_by_scope_and_status(coworld_store, harness):
    make_goal(coworld_store, harness)
    make_goal(coworld_store, harness, title="Other")
    directives.add(coworld_store, harness, text="a", scope="goal:1", source="s")
    directives.add(coworld_store, harness, text="b", scope="goal:1", source="s")
    directives.add(coworld_store, harness, text="c", scope="goal:2", source="s")
    directives.retire(coworld_store, harness, 2)
    assert [d["id"] for d in directives.list(coworld_store)] == ["D-1", "D-3"]
    assert [d["text"] for d in directives.list(coworld_store, scope="goal:1")] == ["a"]
    assert [d["id"] for d in directives.list(coworld_store, status=None)] == ["D-1", "D-2", "D-3"]
    assert [d["id"] for d in directives.list(coworld_store, status="retired")] == ["D-2"]
    assert directives.list(coworld_store, scope="goal:7") == []
    with pytest.raises(StoreError):
        directives.list(coworld_store, status="bogus")
    assert directives.read(coworld_store, 2)["status"] == "retired"
    with pytest.raises(NotFound):
        directives.read(coworld_store, 9)
