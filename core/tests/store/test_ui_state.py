"""UI state (A.10, A.11): one JSON document per conversation, changed by RFC 6902 patches."""

from __future__ import annotations

import pytest

from research_core.store import ui_state
from research_core.store.errors import StoreError

T0 = "2026-10-28T21:04:05.123456Z"


def test_get_of_an_unknown_conversation_is_an_empty_document(workspace_store):
    assert ui_state.get(workspace_store, "workspace") == {
        "conversation": "workspace",
        "doc": {},
        "version": 0,
        "updated": None,
    }


def test_patch_add_replace_remove_and_test(workspace_store, session_actor):
    record = ui_state.patch(
        workspace_store,
        session_actor,
        "workspace",
        [
            {"op": "add", "path": "/panels", "value": {}},
            {"op": "add", "path": "/panels/main", "value": {"kind": "markdown", "content": "hi"}},
            {"op": "replace", "path": "/panels/main/content", "value": "hello"},
            {"op": "test", "path": "/panels/main/kind", "value": "markdown"},
            {"op": "add", "path": "/needs", "value": ["a", "b"]},
            {"op": "remove", "path": "/needs/0"},
        ],
    )
    assert record == {
        "conversation": "workspace",
        "doc": {"panels": {"main": {"kind": "markdown", "content": "hello"}}, "needs": ["b"]},
        "version": 1,
        "updated": T0,
    }
    assert ui_state.get(workspace_store, "workspace") == record
    assert workspace_store.unmarked_changes() == []


def test_patch_bumps_the_version_once_per_call_and_emits_ui_patch(workspace_store, session_actor):
    ops = [{"op": "add", "path": "/a", "value": 1}, {"op": "add", "path": "/b", "value": 2}]
    ui_state.patch(workspace_store, session_actor, "workspace", ops)
    second = ui_state.patch(
        workspace_store, session_actor, "workspace", [{"op": "replace", "path": "/a", "value": 3}]
    )
    assert second["version"] == 2
    assert second["doc"] == {"a": 3, "b": 2}
    events = workspace_store.events_after(0)
    assert [(e.kind, e.subject) for e in events] == [
        ("ui.patch", "workspace"),
        ("ui.patch", "workspace"),
    ]
    assert events[0].payload == {"conversation": "workspace", "version": 1, "ops": ops}
    assert events[1].payload["version"] == 2


def test_patch_unescapes_json_pointer_tokens(workspace_store, session_actor):
    record = ui_state.patch(
        workspace_store,
        session_actor,
        "workspace",
        [
            {"op": "add", "path": "/a~1b", "value": 1},
            {"op": "add", "path": "/m~0n", "value": 2},
            {"op": "add", "path": "/x~01", "value": 3},
        ],
    )
    assert record["doc"] == {"a/b": 1, "m~n": 2, "x~1": 3}


def test_patch_list_indexes_and_append(workspace_store, session_actor):
    record = ui_state.patch(
        workspace_store,
        session_actor,
        "workspace",
        [
            {"op": "add", "path": "/items", "value": ["a", "c"]},
            {"op": "add", "path": "/items/1", "value": "b"},
            {"op": "add", "path": "/items/-", "value": "d"},
            {"op": "replace", "path": "/items/0", "value": "A"},
            {"op": "test", "path": "/items/3", "value": "d"},
        ],
    )
    assert record["doc"] == {"items": ["A", "b", "c", "d"]}


def test_root_replace_takes_a_whole_object(workspace_store, session_actor):
    ui_state.patch(workspace_store, session_actor, "s1", [{"op": "add", "path": "/a", "value": 1}])
    record = ui_state.patch(
        workspace_store, session_actor, "s1", [{"op": "replace", "path": "", "value": {"b": 2}}]
    )
    assert record["doc"] == {"b": 2}
    with pytest.raises(StoreError) as info:
        ui_state.patch(
            workspace_store, session_actor, "s1", [{"op": "add", "path": "", "value": 3}]
        )
    assert "object" in info.value.problems[0]
    with pytest.raises(StoreError):
        ui_state.patch(workspace_store, session_actor, "s1", [{"op": "remove", "path": ""}])


@pytest.mark.parametrize(
    "op",
    [
        {"op": "add", "path": "/missing/child", "value": 1},
        {"op": "add", "path": "/items/5", "value": 1},
        {"op": "add", "path": "/items/x", "value": 1},
        {"op": "add", "path": "/items/-/deeper", "value": 1},
        {"op": "replace", "path": "/absent", "value": 1},
        {"op": "remove", "path": "/absent"},
        {"op": "remove", "path": "/items/2"},
        {"op": "add", "path": "/scalar/child", "value": 1},
        {"op": "add", "path": "no-leading-slash", "value": 1},
        {"op": "test", "path": "/absent", "value": 1},
    ],
)
def test_invalid_paths_are_refused_and_nothing_is_written(workspace_store, session_actor, op):
    ui_state.patch(
        workspace_store,
        session_actor,
        "workspace",
        [
            {"op": "add", "path": "/items", "value": [1, 2]},
            {"op": "add", "path": "/scalar", "value": 1},
        ],
    )
    before = ui_state.get(workspace_store, "workspace")
    with pytest.raises(StoreError) as info:
        ui_state.patch(
            workspace_store,
            session_actor,
            "workspace",
            [{"op": "add", "path": "/first", "value": True}, op],
        )
    assert info.value.problems[0].startswith("op 1 (")
    assert ui_state.get(workspace_store, "workspace") == before
    assert workspace_store.last_seq() == 1


def test_a_failed_test_op_rolls_the_whole_patch_back(workspace_store, session_actor):
    ui_state.patch(workspace_store, session_actor, "s1", [{"op": "add", "path": "/a", "value": 1}])
    with pytest.raises(StoreError) as info:
        ui_state.patch(
            workspace_store,
            session_actor,
            "s1",
            [
                {"op": "replace", "path": "/a", "value": 2},
                {"op": "test", "path": "/a", "value": 1},
            ],
        )
    assert info.value.problems == ["op 1 (test /a): the value differs"]
    assert ui_state.get(workspace_store, "s1")["doc"] == {"a": 1}
    assert ui_state.get(workspace_store, "s1")["version"] == 1


def test_patch_validates_the_ops_shape_at_once(workspace_store, session_actor):
    with pytest.raises(StoreError) as info:
        ui_state.patch(
            workspace_store,
            session_actor,
            "workspace",
            [
                {"op": "move", "from": "/a", "path": "/b"},
                {"op": "add", "path": "/a"},
                {"path": "/a", "value": 1},
                "not an op",
                {"op": "remove", "path": 3},
            ],
        )
    assert len(info.value.problems) == 5
    assert all(p.startswith("op ") for p in info.value.problems)
    with pytest.raises(StoreError):
        ui_state.patch(workspace_store, session_actor, "workspace", [])
    with pytest.raises(StoreError):
        ui_state.patch(
            workspace_store, session_actor, "", [{"op": "add", "path": "/a", "value": 1}]
        )
    assert workspace_store.last_seq() == 0


def test_conversations_are_independent(workspace_store, session_actor):
    ui_state.patch(
        workspace_store, session_actor, "workspace", [{"op": "add", "path": "/a", "value": 1}]
    )
    ui_state.patch(workspace_store, session_actor, "s1", [{"op": "add", "path": "/b", "value": 2}])
    assert ui_state.get(workspace_store, "workspace")["doc"] == {"a": 1}
    assert ui_state.get(workspace_store, "s1")["doc"] == {"b": 2}
    assert ui_state.get(workspace_store, "s1")["version"] == 1


def test_the_coworld_store_table_also_works_but_is_unused(coworld_store, session_actor):
    """Both migrations have ui_state; the workspace store holds the rows (module docstring)."""
    record = ui_state.patch(
        coworld_store, session_actor, "workspace", [{"op": "add", "path": "/a", "value": 1}]
    )
    assert record["version"] == 1
