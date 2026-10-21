import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "completion", Path(__file__).resolve().parents[2] / "plugins/researcher_guard/__init__.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_completion_requires_consistent_journal_evidence():
    def record(_):
        return {"ok": True, "data": {"meta": {"card": "t_upload", "outcome": "partial"}}}

    assert module.completion_problem({"task_id": "t_upload"}, record)
    args = {
        "task_id": "t_upload",
        "metadata": {"journal": "ar://journal/J-test", "outcome": "done"},
    }
    assert module.completion_problem(args, record)
    args["metadata"]["outcome"] = "partial"
    args["metadata"]["version_id"] = "V-local-without-upload"
    assert module.completion_problem(args, record) is None
    args["task_id"] = "another-task"
    assert module.completion_problem(args, record)


def test_successful_upload_requires_receipt():
    record = lambda _: {"ok": True, "data": {"meta": {"card": "t_upload", "outcome": "done"}}}
    args = {
        "task_id": "t_upload",
        "metadata": {"journal": "ar://journal/J-test", "outcome": "done", "version_id": "V-local"},
    }
    assert module.completion_problem(args, record)
    args["metadata"]["platform_policy_version_id"] = "policy:v1"
    assert module.completion_problem(args, record) is None


def test_native_completion_tool_blocks_missing_handoff():
    verdict = module.pre_tool_call(
        tool_name="kanban_complete", args={"task_id": "t_upload", "summary": "Partial"}
    )
    assert verdict["action"] == "block"


def test_completion_reads_routed_profile_workspace(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    home = tmp_path / "researcher"
    monkeypatch.setenv("RESEARCH_WORKSPACE", "/wrong/orchestrator/workspace")
    monkeypatch.setenv("PYTHONPATH", "/wrong/python")
    monkeypatch.setitem(
        sys.modules,
        "agent.secret_scope",
        SimpleNamespace(
            build_profile_secret_scope=lambda _: {},
            current_secret_scope=lambda: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules, "hermes_constants", SimpleNamespace(get_hermes_home=lambda: home)
    )

    def run(command, **kwargs):
        assert command[-1] == str(home / "workspace")
        assert "PYTHONPATH" not in kwargs["env"]
        assert "RESEARCH_WORKSPACE" not in kwargs["env"]
        return SimpleNamespace(
            stdout='{"ok":true,"data":{"meta":{"card":"t1","outcome":"partial"}}}'
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    assert (
        module.pre_tool_call(
            tool_name="kanban_complete",
            args={
                "task_id": "t1",
                "metadata": {"outcome": "partial", "journal": "ar://journal/J-one"},
            },
        )
        is None
    )


def test_registered_completion_schema_exposes_guard_contract(monkeypatch):
    import sys
    from types import SimpleNamespace

    import jsonschema
    import pytest

    # Native free-form metadata cannot describe the researcher's required fields.
    native = SimpleNamespace(
        schema={
            "name": "kanban_complete",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "required": [],
            },
        },
        toolset="kanban",
        handler=object(),
        check_fn=object(),
        requires_env=[],
        is_async=False,
        emoji="",
    )
    monkeypatch.setitem(sys.modules, "tools", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "tools.kanban_tools", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "tools.registry",
        SimpleNamespace(registry=SimpleNamespace(get_entry=lambda name: native)),
    )
    registration = {}
    hooks = {}
    ctx = SimpleNamespace(
        register_tool=lambda **kw: registration.update(kw),
        register_hook=lambda name, fn: hooks.update({name: fn}),
    )
    module.register(ctx)
    schema = registration["schema"]["parameters"]
    jsonschema.Draft202012Validator.check_schema(schema)
    assert registration["handler"] is native.handler
    assert registration["check_fn"] is native.check_fn
    assert registration["override"] is True
    assert hooks["pre_tool_call"] is module.pre_tool_call
    assert native.schema["parameters"]["properties"]["metadata"] == {"type": "object"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"task_id": "t_upload", "summary": "Built", "metadata": {}}, schema)
    handoff = {
        "task_id": "t_upload",
        "summary": "Built and uploaded",
        "metadata": {
            "outcome": "done",
            "journal": "ar://journal/J-test",
            "version_id": "V-local",
            "platform_policy_version_id": "policy:v1",
            "experiment": None,
            "awaiting": None,
        },
    }
    jsonschema.validate(handoff, schema)
    assert (
        module.completion_problem(
            handoff,
            lambda _: {"ok": True, "data": {"meta": {"card": "t_upload", "outcome": "done"}}},
        )
        is None
    )
    # Strict providers may emit null for optional upload fields on non-upload tasks.
    handoff["metadata"].update(version_id=None, platform_policy_version_id=None)
    jsonschema.validate(handoff, schema)
    assert (
        module.completion_problem(
            handoff,
            lambda _: {"ok": True, "data": {"meta": {"card": "t_upload", "outcome": "done"}}},
        )
        is None
    )
