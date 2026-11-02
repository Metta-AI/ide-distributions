import json

import pytest

from research_core.store.actors import Actor
from research_core.store.errors import StoreError
from research_core.tools import base, catalog
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec


def make_ctx(tmp_path, role="orchestrator"):
    return ToolContext.from_env(role, {"IDE_WORKSPACE": str(tmp_path), "IDE_SESSION_ID": "s1"})


@pytest.fixture
def demo_tool():
    def handler(call: ToolCall) -> dict:
        if call.action == "boom":
            raise ToolError(["it broke", "and more"], hint="try again")
        if call.action == "store":
            raise StoreError(["bad write"])
        if call.action == "crash":
            raise RuntimeError("unexpected")
        return {"record": {"id": "G-1", "action": call.action, "args": call.args}}

    spec = ToolSpec(
        name="demo",
        description="A demo tool.",
        handler=handler,
        actions=(
            ActionSpec(
                "create",
                "Make one.",
                {"title": {"type": "string"}},
                ("title",),
                frozenset({"orchestrator"}),
            ),
            ActionSpec(
                "read",
                "Read one.",
                {"goal": {"type": "string"}, "limit": {"type": "integer"}},
                ("goal",),
            ),
            ActionSpec("boom", "Fail loudly."),
            ActionSpec("store", "Fail in the store."),
            ActionSpec("crash", "Crash."),
        ),
        params={"count": {"type": "integer"}, "mode": {"type": "string", "enum": ["a", "b"]}},
    )
    catalog.TOOLS.pop("demo", None)
    catalog.register(spec)
    yield spec
    catalog.TOOLS.pop("demo", None)


def test_schema_shape_follows_the_role(demo_tool):
    schema = demo_tool.schema("researcher")
    assert set(schema) == {"name", "description", "parameters"}
    params = schema["parameters"]
    assert params["required"] == ["action"]
    assert params["additionalProperties"] is False
    assert "create" not in params["properties"]["action"]["enum"]
    assert "read" in params["properties"]["action"]["enum"]
    assert "title" not in params["properties"]
    assert set(params["properties"]) >= {"action", "goal", "count", "mode"}
    assert (
        "create" in demo_tool.schema("orchestrator")["parameters"]["properties"]["action"]["enum"]
    )


def test_validate_reports_every_problem_at_once(demo_tool):
    action, problems = base.validate_args(
        demo_tool, "orchestrator", {"action": "create", "bogus": 1, "count": "x", "mode": "z"}
    )
    assert action == "create"
    assert sorted(problems) == sorted(
        [
            "unknown field 'bogus'",
            "title is required for create",
            "count must be integer",
            "mode must be one of a, b",
        ]
    )


def test_validate_action_gating(demo_tool):
    _, problems = base.validate_args(demo_tool, "researcher", {"action": "create", "title": "x"})
    assert problems == [
        "action 'create' is not available to the researcher; allowed: read, boom, store, crash"
    ]
    _, problems = base.validate_args(demo_tool, "researcher", {"action": "nope"})
    assert problems[0].startswith("unknown action 'nope'")
    _, problems = base.validate_args(demo_tool, "researcher", {})
    assert problems[0].startswith("action is required")


def test_run_tool_success_and_errors(demo_tool, tmp_path):
    ctx = make_ctx(tmp_path)
    ok = json.loads(base.run_tool("demo", {"action": "read", "goal": "G-1"}, ctx))
    assert ok["ok"] is True and ok["record"]["action"] == "read"

    refused = json.loads(base.run_tool("demo", {"action": "boom"}, ctx))
    assert refused == {
        "ok": False,
        "error": "it broke; and more",
        "problems": ["it broke", "and more"],
        "context": {"tool": "demo", "hint": "try again"},
    }
    store = json.loads(base.run_tool("demo", {"action": "store"}, ctx))
    assert store["problems"] == ["bad write"]
    crash = json.loads(base.run_tool("demo", {"action": "crash"}, ctx))
    assert crash["error"] == "RuntimeError: unexpected" and "traceback" in crash["context"]
    invalid = json.loads(base.run_tool("demo", {"action": "read"}, ctx))
    assert invalid["problems"] == ["goal is required for read"]
    unknown = json.loads(base.run_tool("nope", {}, ctx))
    assert unknown["ok"] is False


def test_a_field_the_action_does_not_take_is_refused(demo_tool):
    _, problems = base.validate_args(
        demo_tool, "orchestrator", {"action": "read", "goal": "G-1", "title": "x", "count": 2}
    )
    assert problems == ["title is not a field of read"]


def test_empty_placeholders_for_other_actions_fields_are_dropped(demo_tool, tmp_path):
    ok = json.loads(
        base.run_tool("demo", {"action": "read", "goal": "G-1", "title": ""}, make_ctx(tmp_path))
    )
    assert ok["ok"] is True
    assert ok["record"]["args"] == {"action": "read", "goal": "G-1"}


def test_an_empty_placeholder_skips_type_and_enum_checks(demo_tool):
    # "limit" is an integer field of read; an empty one on create is padding, not a type error.
    _, problems = base.validate_args(
        demo_tool, "orchestrator", {"action": "create", "title": "x", "limit": ""}
    )
    assert problems == []


def test_argument_error_says_what_arrived_and_how_to_fix_it(demo_tool, tmp_path):
    refused = json.loads(
        base.run_tool("demo", {"action": "create", "count": 3}, make_ctx(tmp_path))
    )
    assert refused["ok"] is False
    assert refused["problems"] == ["title is required for create"]
    assert refused["error"].startswith("demo create was not run, nothing changed")
    assert "This call sent: count." in refused["error"]
    assert refused["context"] == {"tool": "demo", "action": "create", "received": ["count"]}
    assert refused["usage"]["does"] == "Make one."
    assert list(refused["usage"]["required"]) == ["title"]
    assert set(refused["usage"]["optional"]) == {"count", "mode"}
    assert refused["usage"]["example"] == {"action": "create", "title": "<text>"}


def test_argument_error_without_an_action_lists_the_actions(demo_tool, tmp_path):
    refused = json.loads(base.run_tool("demo", {"goal": "G-1"}, make_ctx(tmp_path, "researcher")))
    actions = refused["usage"]["actions"]
    assert "create" not in actions
    assert actions["read"] == {"does": "Read one.", "requires": ["goal"]}
    assert "usage.actions" in refused["error"] and "usage.example" not in refused["error"]


def test_run_tool_scrubs_workspace_paths(demo_tool, tmp_path):
    ctx = make_ctx(tmp_path)

    def leaky(call):
        raise ToolError(f"cannot open {tmp_path}/.state/store.db")

    spec = ToolSpec(name="leaky", description="x", handler=leaky)
    catalog.register(spec)
    try:
        text = base.run_tool("leaky", {}, ctx)
    finally:
        catalog.TOOLS.pop("leaky")
    assert str(tmp_path / ".state") not in text and "<state>" in text


def test_context_from_env(tmp_path):
    ctx = make_ctx(tmp_path, "researcher")
    assert ctx.actor == Actor("session", "s1")
    assert ctx.workspace.root == tmp_path
    assert ctx.hermes_session_id() == "s1"
    with pytest.raises(ToolError):
        ctx.coworld()
    assert ctx.coworld_or_none() is None
