import research_core.tools.goal  # noqa: F401  (registers the tool)

CREATE = {
    "action": "create",
    "title": "Beat the house",
    "end_state": "Our champion ranks first in the division",
    "reason": "The user asked for it",
}


def test_create_read_update_status_roundtrip(call):
    created = call("goal", CREATE)
    assert created["ok"] is True and created["record"]["id"] == "G-1"
    assert created["record"]["status"] == "active"

    read = call("goal", {"action": "read", "goal": "G-1"}, role="researcher")
    assert read["record"]["title"] == "Beat the house"
    assert "tasks" in read["record"]

    updated = call("goal", {"action": "update", "goal": 1, "where_it_stands": "Not started"})
    assert updated["record"]["where_it_stands"] == "Not started"

    parked = call(
        "goal",
        {"action": "set_status", "goal": "G-1", "status": "parked", "status_reason": "waiting"},
    )
    assert parked["record"]["status"] == "parked"

    listed = call("goal", {"action": "list", "status": "parked"}, role="researcher")
    assert [g["id"] for g in listed["records"]] == ["G-1"]


def test_researcher_cannot_create(call):
    result = call("goal", CREATE, role="researcher")
    assert result["ok"] is False
    assert "not available to the researcher" in result["error"]


def test_every_problem_at_once(call):
    result = call("goal", {"action": "create", "title": ""})
    assert result["ok"] is False
    assert {
        "title is required for create",
        "end_state is required for create",
        "reason is required for create",
    } <= set(result["problems"])


def test_bad_reference(call):
    result = call("goal", {"action": "read", "goal": "T-3"})
    assert result["ok"] is False and "goal reference" in result["error"]


def test_missing_goal_is_a_clean_refusal(call):
    result = call("goal", {"action": "read", "goal": "G-99"})
    assert result["ok"] is False and "G-99" in result["error"]
