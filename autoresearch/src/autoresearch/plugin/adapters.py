"""Hermes hook adapters. STDLIB ONLY. Task 4.1 implements the real policy.

Stub: only `post_tool_call` does anything (it appends an event line). A plugin
must never break the agent, so every hook swallows all exceptions.
"""

import json
import os
import time

from autoresearch import runctx

TOOL_EVENTS_FILE = "tool_events.jsonl"


def _hermes_home() -> str | None:
    """Profile home: Hermes' resolver inside Hermes (amendment A7), else HERMES_HOME."""
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except ImportError:
        return os.environ.get("HERMES_HOME")


def _run_context() -> runctx.RunContext:
    env = dict(os.environ)
    home = _hermes_home()
    if home:
        env[runctx.ENV_HERMES_HOME] = home
    return runctx.from_env(env)


def pre_tool_call(tool_name=None, args=None, task_id=None, **kwargs):
    return None


def post_tool_call(
    tool_name=None, args=None, status=None, error_type=None, error_message=None,
    duration_ms=None, **kwargs,
):
    try:
        directory = runctx.runtime_dir(_run_context())
        directory.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": time.time(), "tool": tool_name, "status": status, "error_type": error_type,
            "duration_ms": duration_ms,
        }
        with (directory / TOOL_EVENTS_FILE).open("a") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
    except Exception:  # noqa: BLE001, S110 - a plugin must never break the agent
        pass


def on_session_end(
    session_id=None, completed=None, failed=None, interrupted=None, turn_exit_reason=None,
    **kwargs,
):
    return None


def pre_llm_call(is_first_turn=False, **kwargs):
    return None


def kanban_task_completed(task_id=None, run_id=None, **kwargs):
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("kanban_task_completed", kanban_task_completed)
