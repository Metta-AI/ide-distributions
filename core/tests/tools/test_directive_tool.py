import research_core.tools.directive  # noqa: F401
import research_core.tools.goal  # noqa: F401


def test_workspace_and_goal_directives(call):
    ws = call(
        "directive",
        {"action": "add", "text": "Always ask before spending credits", "source": "msg-1"},
    )
    assert ws["ok"] is True and ws["record"]["scope"] == "workspace"

    call("goal", {"action": "create", "title": "t", "end_state": "e", "reason": "r"})
    goal_scoped = call(
        "directive",
        {"action": "add", "text": "Only test on seeds 1-10", "scope": "G-1", "source": "msg-2"},
    )
    assert goal_scoped["record"]["scope"] == "goal:1"

    everything = call("directive", {"action": "list"}, role="researcher")
    assert sorted(d["scope"] for d in everything["records"]) == ["goal:1", "workspace"]
    only_ws = call("directive", {"action": "list", "scope": "workspace"})
    assert [d["scope"] for d in only_ws["records"]] == ["workspace"]
    only_goal = call("directive", {"action": "list", "scope": "G-1"})
    assert [d["scope"] for d in only_goal["records"]] == ["goal:1"]


def test_update_and_retire_find_the_right_store(call):
    call("directive", {"action": "add", "text": "a", "source": "m"})
    updated = call("directive", {"action": "update", "directive": "D-1", "text": "b"})
    assert updated["record"]["text"] == "b"
    retired = call("directive", {"action": "retire", "directive": 1})
    assert retired["record"]["status"] == "retired"
    assert call("directive", {"action": "list"})["records"] == []
    missing = call("directive", {"action": "retire", "directive": "D-7"})
    assert missing["ok"] is False and "D-7" in missing["error"]


def test_bad_scope_and_role(call):
    bad = call("directive", {"action": "add", "text": "x", "scope": "T-1", "source": "m"})
    assert bad["ok"] is False and "scope must be" in bad["error"]
    denied = call("directive", {"action": "add", "text": "x", "source": "m"}, role="researcher")
    assert denied["ok"] is False
