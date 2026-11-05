"""Actions with a tool of their own: required fields, the grouped handler, old calls still run."""

import json

import pytest

from research_core.tools import catalog
from research_core.tools.base import run_tool, usage
from tests.model import harness, write_actions


def tools():
    catalog.tools_for("orchestrator")
    return catalog.TOOLS


CLAIM = {
    "summary": "pv-42 leads league L-7.",
    "body": "The league page shows pv-42 at rank 1.",
    "tags": ["champion"],
    "evidence": [
        {
            "type": "doc",
            "ref": "https://softmax.com/leagues/L-7 (retrieved 2026-11-05): pv-42",
            "stance": "supports",
        }
    ],
}


def test_the_schema_marks_required_fields_as_required_and_takes_no_action():
    parameters = tools()["claim_write"].schema("orchestrator")["parameters"]
    assert parameters["required"] == ["summary", "body", "tags", "evidence"]
    assert "action" not in parameters["properties"]
    assert parameters["additionalProperties"] is False


def test_the_grouped_tool_stops_offering_the_action_and_names_its_tool():
    schema = tools()["claim"].schema("orchestrator")
    assert "write" not in schema["parameters"]["properties"]["action"]["enum"]
    assert "summary" not in schema["parameters"]["properties"]
    assert "claim_write" in schema["description"]


def test_roles_and_executor_come_from_the_action_and_the_grouped_tool():
    assert tools()["task_wait"].roles == frozenset({"researcher"})
    assert tools()["task_abandon"].roles == frozenset({"orchestrator"})
    assert tools()["claim_write"].executor == tools()["claim"].executor


def test_a_call_runs_the_grouped_handler(call, ctx_factory):
    written = json.loads(run_tool("claim_write", CLAIM, ctx_factory("orchestrator")))
    assert written["ok"] is True
    assert call("claim", {"action": "read", "claim": written["record"]["id"]})["ok"] is True


def test_a_call_naming_the_old_action_still_runs(ctx_factory):
    """Stored chats show the old shape, and a model may copy it after its tools refresh."""
    old_call = {"action": "write", **CLAIM}
    assert json.loads(run_tool("claim", old_call, ctx_factory("orchestrator")))["ok"] is True


def test_an_old_call_is_still_refused_for_what_it_leaves_out(ctx_factory):
    refused = json.loads(run_tool("claim", {"action": "write"}, ctx_factory("orchestrator")))
    assert "summary is required for write" in refused["problems"]


def test_a_refusal_names_the_tool_and_what_is_missing(ctx_factory):
    refused = json.loads(run_tool("claim_write", {"body": "x"}, ctx_factory("orchestrator")))
    assert refused["error"].startswith("claim_write was not run, nothing changed:")
    assert "summary is required" in refused["problems"]
    assert "Shape:" in refused["usage"]["required"]["evidence"]


def test_a_refusals_example_keeps_the_actions_extra_fields():
    """``goal`` is not required of ``task_propose`` by the schema, but the handler needs it."""
    spec = tools()["task_propose"]
    action = tools()["task"].action("propose")
    assert spec.example_fields == action.example_fields
    assert set(action.example_fields) <= set(usage(spec, "researcher", None)["example"])


def test_a_refusal_of_a_call_without_action_lists_only_what_the_grouped_tool_offers():
    shown = usage(tools()["claim"], "orchestrator", None)["actions"]
    assert "write" not in shown and "search" in shown


def test_a_refusal_from_an_own_tool_shows_the_flat_call_and_no_action(ctx_factory):
    refused = json.loads(run_tool("task_ask", {}, ctx_factory("researcher")))
    assert refused["usage"]["example"] == {"task": "<such as T-3>", "question": "<text>"}
    assert "action" not in refused["context"]
    assert "action" not in refused["error"]


def test_an_own_tool_refuses_a_call_naming_an_action(ctx_factory):
    sent = {"action": "write", **CLAIM}
    refused = json.loads(run_tool("claim_write", sent, ctx_factory("orchestrator")))
    assert refused["ok"] is False
    assert "unknown field 'action'" in refused["problems"]


def test_a_refused_old_call_is_shown_the_own_tool(ctx_factory):
    refused = json.loads(run_tool("claim", {"action": "add_evidence"}, ctx_factory("orchestrator")))
    assert refused["error"].startswith("claim_add_evidence was not run")
    assert refused["context"]["tool"] == "claim_add_evidence"
    assert "action" not in refused["usage"].get("example", {})


def test_a_call_without_action_is_told_about_the_own_tools(ctx_factory):
    refused = json.loads(run_tool("claim", {}, ctx_factory("orchestrator")))
    assert refused["usage"]["own_tools"] == ["claim_write", "claim_add_evidence"]
    assert "usage.own_tools" in refused["error"]


@pytest.mark.parametrize(
    ("args", "outcome"), [(CLAIM, "succeeded"), ({"body": "x"}, "validation_rejected")]
)
def test_telemetry_reports_an_own_tool_as_the_grouped_tool_and_action(
    ctx_factory, capsys, args, outcome
):
    ctx = ctx_factory("orchestrator")
    capsys.readouterr()
    run_tool("claim_write", args, ctx)
    (event,) = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert (event["tool"], event["action"], event["outcome"]) == ("claim", "write", outcome)


def test_telemetry_does_not_name_an_own_tool_the_role_lacks(ctx_factory, capsys):
    ctx = ctx_factory("orchestrator")
    capsys.readouterr()
    run_tool("task_wait", {}, ctx)
    (event,) = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert (event["tool"], event["action"], event["outcome"]) == ("unknown", None, "unknown_tool")


def test_own_tool_text_stands_alone():
    """Its description and fields do not say action, and name no other action bare."""
    for spec in tools().values():
        if spec.split_from is None:
            continue
        schema = spec.schema(next(iter(sorted(spec.roles))))
        fields = [p.get("description", "") for p in schema["parameters"]["properties"].values()]
        for text in (schema["description"], *fields):
            assert "action" not in text.lower(), (spec.name, text)


@pytest.mark.parametrize("extra", [{"action": "ask"}, {"question_id": None}])
def test_the_model_tests_see_what_a_flat_tool_refuses(extra):
    args = {"task": "T-1", "question": "Which?", **extra}
    assert harness.own_tool_problems("researcher", "task_ask", args)
    assert not harness.own_tool_problems("researcher", "task_ask", {"task": "T-1", "question": "?"})
    assert harness.ShippedSchemas().canonical("researcher", "task_ask", args)[0] == "task"


def test_a_flat_refusal_lists_action_among_what_it_received(ctx_factory):
    refused = json.loads(run_tool("task_ask", {"action": "ask"}, ctx_factory("researcher")))
    assert refused["context"]["received"] == ["action"]


def test_the_model_tests_answer_a_detour_as_the_flat_tool_refuses_it():
    case = next(c for c in write_actions.CASES if c.tool == "task" and c.role == "researcher")
    args = {"action": "ask", "task": "T-1", "question": "Which?"}
    refused = json.loads(write_actions.answer(case, harness.ShippedSchemas(), "task_ask", args))
    assert "unknown field 'action'" in refused["problems"]
