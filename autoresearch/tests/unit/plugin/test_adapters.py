"""Hermes hook adapters: directive shapes, briefing injection, event logs, never raising."""

import json

import pytest

from autoresearch.plugin import adapters, policy


@pytest.fixture
def worker_env(layout, monkeypatch):
    """A card worker for task t_1 in the layout's worker worktree."""
    monkeypatch.setenv("HERMES_HOME", str(layout.hermes_home))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "r_1")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(layout.worker))
    runtime = layout.canonical / ".supervisor" / "runtime"
    (runtime / "cards").mkdir(parents=True)
    (runtime / "briefings").mkdir(parents=True)
    return runtime


def _write_card(runtime, **fields):
    card = {"task_id": "t_1", "request_id": "Q-1", "mode": "investigation", "refs": [],
            "finished": False, "responsible_inbound": []} | fields
    (runtime / "cards" / "t_1.json").write_text(json.dumps(card))


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_pre_tool_call_blocks_with_the_hermes_directive_shape(worker_env):
    _write_card(worker_env)
    directive = adapters.pre_tool_call(
        tool_name="terminal", args={"command": "coworld player use X"}, task_id="s", extra=1,
    )
    assert directive["action"] == "block"
    assert directive["message"].startswith("autoresearch policy:")
    assert set(directive) == {"action", "message"}


def test_pre_tool_call_allows_with_none(worker_env):
    _write_card(worker_env)
    assert adapters.pre_tool_call(tool_name="terminal", args={"command": "ls"}) is None
    assert adapters.pre_tool_call() is None


def test_pre_tool_call_reads_the_card_file_each_call(worker_env):
    _write_card(worker_env)
    write = {"path": "notes/x.md", "content": "x"}
    assert adapters.pre_tool_call(tool_name="write_file", args=write) is None
    _write_card(worker_env, finished=True)
    assert adapters.pre_tool_call(tool_name="write_file", args=write)["action"] == "block"


def test_memory_follows_card_mode(worker_env):
    memory = {"action": "add", "content": "x"}
    _write_card(worker_env, mode="planning")
    assert adapters.pre_tool_call(tool_name="memory", args=memory) is None
    _write_card(worker_env, mode="experimentation")
    assert adapters.pre_tool_call(tool_name="memory", args=memory)["action"] == "block"


def test_missing_card_file_means_global_rules_only(worker_env):
    assert adapters.pre_tool_call(tool_name="memory", args={"action": "add"}) is None
    blocked = adapters.pre_tool_call(tool_name="terminal", args={"command": "softmax login"})
    assert blocked["action"] == "block"


def test_corrupt_card_keeps_global_blocks_and_logs(worker_env):
    (worker_env / "cards" / "t_1.json").write_text("{not json")
    blocked = adapters.pre_tool_call(tool_name="terminal", args={"command": "coworld submit p"})
    assert blocked["action"] == "block"
    assert adapters.pre_tool_call(tool_name="terminal", args={"command": "ls"}) is None
    errors = _lines(worker_env / "plugin_errors.jsonl")
    assert errors[0]["hook"] == "pre_tool_call" and errors[0]["task"] == "t_1"


def test_policy_crash_allows_and_logs(worker_env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("policy exploded")

    monkeypatch.setattr(policy, "tool_policy", boom)
    assert adapters.pre_tool_call(tool_name="terminal", args={"command": "ls"}) is None
    assert "policy exploded" in _lines(worker_env / "plugin_errors.jsonl")[0]["error"]


def test_unparsed_shell_is_noted(worker_env):
    assert adapters.pre_tool_call(tool_name="terminal", args={"command": 'echo "x'}) is None
    note = _lines(worker_env / "plugin_errors.jsonl")[0]
    assert note["kind"] == "policy_note" and "unparsed" in note["error"]


def test_no_hermes_home_never_raises(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert adapters.pre_tool_call(tool_name="terminal", args={"command": "ls"}) is None
    assert adapters.post_tool_call(tool_name="terminal") is None
    assert adapters.pre_llm_call(is_first_turn=True) is None
    assert adapters.on_session_end(session_id="s") is None
    assert adapters.kanban_task_completed(task_id="t") is None


def test_pre_llm_call_injects_the_briefing_on_the_first_turn(worker_env):
    marker = "<!-- autoresearch-briefing task=t_1 -->"
    (worker_env / "briefings" / "t_1.md").write_text(f"{marker}\n# Card Q-1\nDo the thing.\n")
    text = adapters.pre_llm_call(
        session_id="s", user_message="work", is_first_turn=True, model="m", platform="cli",
    )
    assert marker in text and "Do the thing." in text
    assert adapters.pre_llm_call(is_first_turn=False, platform="cli") is None


def test_pre_llm_call_records_the_worker_identity_and_names_the_run(worker_env, layout):
    """Worker subprocesses lose HERMES_KANBAN_TASK/_RUN_ID (Hermes scrubs them), so the
    first turn records them for ``runctx`` and tells the model the run id."""
    (worker_env / "briefings" / "t_1.md").write_text("# Card\n")
    text = adapters.pre_llm_call(is_first_turn=True)
    assert "- Run: r_1" in text
    record = json.loads((worker_env / "workers" / "Q-1.json").read_text())
    assert record.pop("started_at") > 0
    assert record == {"task_id": "t_1", "run_id": "r_1", "worker": str(layout.worker)}


def test_a_failed_identity_record_is_logged_and_the_stale_one_removed(worker_env, monkeypatch):
    (worker_env / "briefings" / "t_1.md").write_text("# Card\n")
    stale = worker_env / "workers" / "Q-1.json"
    stale.parent.mkdir()
    stale.write_text('{"task_id": "t_1", "run_id": "r_0"}')

    def broken_replace(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(adapters.runctx.os, "replace", broken_replace)
    text = adapters.pre_llm_call(is_first_turn=True)
    assert "- Run: r_1" in text  # the briefing still goes in
    assert not stale.exists()
    errors = _lines(worker_env / "plugin_errors.jsonl")
    assert errors[0]["hook"] == "pre_llm_call" and "disk full" in errors[0]["error"]


def test_pre_llm_call_adds_a_missing_marker(worker_env):
    (worker_env / "briefings" / "t_1.md").write_text("# Card\n")
    text = adapters.pre_llm_call(is_first_turn=True)
    assert text.splitlines()[0] == "<!-- autoresearch-briefing task=t_1 -->"


def test_pre_llm_call_needs_a_kanban_task(worker_env, monkeypatch):
    (worker_env / "briefings" / "t_1.md").write_text("# Card\n")
    monkeypatch.delenv("HERMES_KANBAN_TASK")
    assert adapters.pre_llm_call(is_first_turn=True) is None


def test_unreadable_briefing_never_raises(worker_env):
    (worker_env / "briefings" / "t_1.md").mkdir()
    assert adapters.pre_llm_call(is_first_turn=True) is None
    assert _lines(worker_env / "plugin_errors.jsonl")[0]["hook"] == "pre_llm_call"


def test_post_tool_call_records_a_tool_event(worker_env):
    adapters.post_tool_call(
        tool_name="terminal", args={"command": "SECRET=x curl --token abc https://h"},
        status="error", error_type="tool_error", error_message="boom", duration_ms=12,
        result="{}", task_id="s", tool_call_id="c", middleware_trace=[], surprise=1,
    )
    event = _lines(worker_env / "tool_events.jsonl")[0]
    assert set(event) == {
        "ts", "task", "run", "tool", "status", "error_type", "duration_ms", "cmd_signature",
    }
    assert (event["task"], event["run"], event["tool"]) == ("t_1", "r_1", "terminal")
    assert (event["status"], event["error_type"], event["duration_ms"]) == (
        "error", "tool_error", 12,
    )
    assert event["cmd_signature"] == "terminal: curl"


def test_session_end_and_task_completed_append_kanban_events(worker_env):
    adapters.on_session_end(
        session_id="s1", completed=True, failed=False, interrupted=False,
        turn_exit_reason="done", platform="cli",
    )
    adapters.kanban_task_completed(
        task_id="t_1", run_id="r_1", board="ar", assignee="ar", profile_name="ar",
        summary="finished",
    )
    events = _lines(worker_env / "kanban_events.jsonl")
    assert [e["event"] for e in events] == ["session_end", "kanban_task_completed"]
    assert events[0]["session_id"] == "s1" and events[0]["task"] == "t_1"
    assert events[1]["task_id"] == "t_1" and events[1]["run_id"] == "r_1"
    assert events[1]["board"] == "ar"


def test_register_registers_five_hooks():
    hooks = {}

    class FakeCtx:
        def register_hook(self, name, callback):
            hooks[name] = callback

    adapters.register(FakeCtx())
    assert hooks == {
        "pre_tool_call": adapters.pre_tool_call,
        "post_tool_call": adapters.post_tool_call,
        "on_session_end": adapters.on_session_end,
        "pre_llm_call": adapters.pre_llm_call,
        "kanban_task_completed": adapters.kanban_task_completed,
    }
