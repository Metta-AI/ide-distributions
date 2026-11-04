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


def _add_first_workspace_and_goal_directives(call):
    workspace = call("directive", {"action": "add", "text": "workspace rule", "source": "m"})
    call("goal", {"action": "create", "title": "t", "end_state": "e", "reason": "r"})
    goal = call("directive", {"action": "add", "text": "goal rule", "scope": "G-1", "source": "m"})
    return workspace["record"], goal["record"]


def _active_rules(call):
    return sorted(record["text"] for record in call("directive", {"action": "list"})["records"])


def test_workspace_and_goal_directives_never_share_an_id(call):
    workspace, goal = _add_first_workspace_and_goal_directives(call)
    assert workspace["id"] == "W-1"
    assert goal["id"] == "D-1"


def test_each_id_reaches_only_its_own_directive(call):
    _add_first_workspace_and_goal_directives(call)

    updated = call("directive", {"action": "update", "directive": "D-1", "text": "new goal rule"})
    assert updated["record"]["scope"] == "goal:1"
    assert _active_rules(call) == ["new goal rule", "workspace rule"]

    retired = call("directive", {"action": "retire", "directive": "W-1"})
    assert retired["record"]["scope"] == "workspace"
    assert _active_rules(call) == ["new goal rule"]

    # The Goal directive's id does not fall through to the retired workspace directive.
    retired = call("directive", {"action": "retire", "directive": "D-1"})
    assert retired["record"]["scope"] == "goal:1"
    assert _active_rules(call) == []


def test_an_id_for_a_directive_that_does_not_exist_is_refused(call):
    call("directive", {"action": "add", "text": "workspace rule", "source": "m"})
    missing = call("directive", {"action": "retire", "directive": "D-1"})
    assert missing["ok"] is False and "D-1" in missing["error"]
    assert _active_rules(call) == ["workspace rule"]


def test_a_bare_number_is_refused_because_it_names_no_store(call):
    call("directive", {"action": "add", "text": "workspace rule", "source": "m"})
    for bare in ("1", 1):
        refused = call("directive", {"action": "retire", "directive": bare})
        assert refused["ok"] is False
        assert "W-" in refused["error"] and "D-" in refused["error"]
    assert _active_rules(call) == ["workspace rule"]


def test_bad_scope_and_role(call):
    bad = call("directive", {"action": "add", "text": "x", "scope": "T-1", "source": "m"})
    assert bad["ok"] is False and "scope must be" in bad["error"]
    denied = call("directive", {"action": "add", "text": "x", "source": "m"}, role="researcher")
    assert denied["ok"] is False
