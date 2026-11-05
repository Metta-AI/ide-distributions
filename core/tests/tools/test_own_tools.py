"""Actions with a tool of their own: required fields, the grouped handler, old calls still run."""

import json

from research_core.tools import catalog
from research_core.tools.base import run_tool, usage


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
