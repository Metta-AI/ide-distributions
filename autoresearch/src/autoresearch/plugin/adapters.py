"""Hermes hook adapters. STDLIB ONLY: imported inside the Hermes process.

Signatures follow Hermes v0.21.5 (`website/docs/user-guide/features/hooks.md`,
`model_tools.py` `_emit_post_tool_call_hook`, `hermes_cli/plugins.py` VALID_HOOKS).

A plugin must never break the agent. Hermes fails *closed* when a `pre_tool_call` callback
raises (the tool is blocked), so every hook here catches everything: an internal error
allows the call (returns None) and is appended to `runtime/plugin_errors.jsonl`.
"""

import json
import os
import time
import traceback
from pathlib import Path

from autoresearch import runctx
from autoresearch.plugin import policy

TOOL_EVENTS_FILE = "tool_events.jsonl"
KANBAN_EVENTS_FILE = "kanban_events.jsonl"
PLUGIN_ERRORS_FILE = "plugin_errors.jsonl"
# Same format as autoresearch.runtime.briefing.MARKER (not imported: that module is not
# stdlib-only).
BRIEFING_MARKER = "<!-- autoresearch-briefing task={task_id} -->"
SUMMARY_MAX = 500

# Card metadata cache: path -> ((mtime_ns, size), card).
_card_cache: dict[Path, tuple[tuple[int, int], dict]] = {}


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


def _append(ctx: runctx.RunContext, filename: str, record: dict) -> None:
    directory = runctx.runtime_dir(ctx)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / filename).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def _log_error(ctx: runctx.RunContext | None, hook: str, error: object, kind: str = "error"):
    """Best effort: without a run context there is nowhere to write."""
    if ctx is None:
        return
    try:
        _append(ctx, PLUGIN_ERRORS_FILE, {
            "ts": time.time(), "hook": hook, "kind": kind, "task": ctx.card_id,
            "run": ctx.run_id, "error": error if isinstance(error, str) else repr(error),
            "trace": traceback.format_exc(limit=5) if isinstance(error, BaseException) else None,
        })
    except Exception:  # noqa: BLE001, S110 - a plugin must never break the agent
        pass


def _load_card(ctx: runctx.RunContext) -> dict | None:
    """`runtime/cards/<task>.json`, or None for a non-card session. Raises on a bad file."""
    if not ctx.card_id:
        return None
    path = runctx.runtime_dir(ctx) / "cards" / f"{ctx.card_id}.json"
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _card_cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    card = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(card, dict):
        raise TypeError(f"{path} is not a JSON object")
    _card_cache[path] = (key, card)
    return card


def pre_tool_call(tool_name=None, args=None, task_id=None, **kwargs):
    ctx = None
    try:
        ctx = _run_context()
        try:
            card = _load_card(ctx)
        except Exception as error:  # noqa: BLE001 - a bad card file keeps the global rules
            _log_error(ctx, "pre_tool_call", error)
            card = None
        allowed, reason = policy.tool_policy(tool_name or "", args or {}, ctx, card)
        if not allowed:
            return {"action": "block", "message": reason}
        if reason:
            _log_error(ctx, "pre_tool_call", f"{tool_name}: {reason}", kind="policy_note")
    except Exception as error:  # noqa: BLE001 - a plugin must never break the agent
        _log_error(ctx, "pre_tool_call", error)
    return None


def post_tool_call(
    tool_name=None, args=None, status=None, error_type=None, error_message=None,
    duration_ms=None, **kwargs,
):
    ctx = None
    try:
        ctx = _run_context()
        _append(ctx, TOOL_EVENTS_FILE, {
            "ts": time.time(), "task": ctx.card_id, "run": ctx.run_id, "tool": tool_name,
            "status": status, "error_type": error_type, "duration_ms": duration_ms,
            "cmd_signature": policy.command_signature(tool_name or "", args or {}),
        })
    except Exception as error:  # noqa: BLE001 - a plugin must never break the agent
        _log_error(ctx, "post_tool_call", error)


def on_session_end(
    session_id=None, completed=None, failed=None, interrupted=None, turn_exit_reason=None,
    **kwargs,
):
    ctx = None
    try:
        ctx = _run_context()
        _append(ctx, KANBAN_EVENTS_FILE, {
            "ts": time.time(), "event": "session_end", "task": ctx.card_id, "run": ctx.run_id,
            "session_id": session_id, "completed": completed, "failed": failed,
            "interrupted": interrupted, "turn_exit_reason": turn_exit_reason,
        })
    except Exception as error:  # noqa: BLE001 - a plugin must never break the agent
        _log_error(ctx, "on_session_end", error)


def pre_llm_call(is_first_turn=False, **kwargs):
    """The card briefing, injected into the first user turn of a Kanban card worker."""
    task = os.environ.get(runctx.ENV_CARD_ID)
    if not is_first_turn or not task:
        return None
    ctx = None
    try:
        ctx = _run_context()
        text = (runctx.runtime_dir(ctx) / "briefings" / f"{task}.md").read_text(encoding="utf-8")
        marker = BRIEFING_MARKER.format(task_id=task)
        return text if marker in text else f"{marker}\n{text}"
    except Exception as error:  # noqa: BLE001 - a plugin must never break the agent
        _log_error(ctx, "pre_llm_call", error)
    return None


def kanban_task_completed(task_id=None, run_id=None, **kwargs):
    ctx = None
    try:
        ctx = _run_context()
        summary = kwargs.get("summary")
        _append(ctx, KANBAN_EVENTS_FILE, {
            "ts": time.time(), "event": "kanban_task_completed", "task_id": task_id,
            "run_id": run_id, "board": kwargs.get("board"), "assignee": kwargs.get("assignee"),
            "profile_name": kwargs.get("profile_name"),
            "summary": summary[:SUMMARY_MAX] if isinstance(summary, str) else summary,
        })
    except Exception as error:  # noqa: BLE001 - a plugin must never break the agent
        _log_error(ctx, "kanban_task_completed", error)


def register(ctx) -> None:
    """Hermes `PluginContext.register_hook(name, callback)` (hermes_cli/plugins.py:912)."""
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("kanban_task_completed", kanban_task_completed)
