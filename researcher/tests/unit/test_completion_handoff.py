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
