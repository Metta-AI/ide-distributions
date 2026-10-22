import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "completion", Path(__file__).resolve().parents[2] / "plugins/researcher_guard/__init__.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_native_completion_does_not_require_research_journal(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert module.pre_tool_call(
        tool_name="kanban_complete", args={"task_id": "t_upload", "summary": "Assignment finished"}
    ) is None


def test_registration_preserves_native_completion_contract():
    class Context:
        def __init__(self):
            self.hooks = []

        def register_hook(self, name, handler):
            self.hooks.append((name, handler))

        def register_tool(self, **kwargs):
            raise AssertionError("Worker completion must keep the native contract")

    context = Context()
    module.register(context)
    assert len(context.hooks) == 1
