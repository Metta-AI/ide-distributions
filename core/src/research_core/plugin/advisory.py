"""The advisory hook and the last-error hook (design C.4, B.4).

``transform_tool_result`` appends a note to the result of a shell command, code snippet or
file access that bypasses a profile tool. It never blocks and never raises. ``post_tool_call``
keeps each Session's last failed tool call on its held Task (``last_error_seen``), best effort.
Both run inside Hermes: stdlib only, store access through the in-process store library.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from research_core.plugin import rules
from research_core.plugin.executor import profile_env
from research_core.tools.catalog import Role

log = logging.getLogger(__name__)

_EXIT_CODE = re.compile(r"(?:exit code|exit_code|returncode)\D{0,4}(\d+)", re.IGNORECASE)


def _broken_capabilities(role: Role, session_id: str) -> dict[str, str]:
    """Capability name → maintenance Task id for broken capabilities; empty on any trouble."""
    try:
        from research_core.store import capabilities
        from research_core.tools.base import ToolContext

        ctx = ToolContext.from_env(role, profile_env(IDE_SESSION_ID=session_id))
        if ctx.coworld_or_none() is None:
            return {}
        out: dict[str, str] = {}
        for row in capabilities.status(ctx.store):
            if row.get("status") == "broken":
                task = row.get("maintenance_task_id")
                out[row["name"]] = f"T-{task}" if task else ""
        return out
    except Exception:  # best effort only
        return {}


def failure_of(result: Any) -> dict | None:
    """What failed in a tool result, if anything: ``{"error", "exit_code"}``; else ``None``."""
    if not isinstance(result, str):
        return None
    text = result.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            if parsed.get("ok") is False or "error" in parsed:
                return {
                    "error": str(parsed.get("error") or parsed.get("problems") or "")[:500],
                    "exit_code": parsed.get("exit_code")
                    or (parsed.get("context") or {}).get("exit_code"),
                }
            return None
    match = _EXIT_CODE.search(text)
    if match and int(match.group(1)) != 0:
        return {"error": text[-500:], "exit_code": int(match.group(1))}
    return None


def make_hooks(role: Role) -> dict[str, Callable[..., Any]]:
    def transform_tool_result(
        tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_: Any
    ) -> str | None:
        try:
            matched = rules.matches(tool_name, args)
            if not matched or not isinstance(result, str):
                return None
            broken = (
                _broken_capabilities(role, session_id) if any(r.capability for r in matched) else {}
            )
            notes = rules.notes_for(tool_name, args, broken)
            return result + "\n\n" + "\n".join(notes) if notes else None
        except Exception as error:  # never break a tool result
            log.debug("advisory hook failed: %s", error)
            return None

    def post_tool_call(
        tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_: Any
    ) -> None:
        try:
            failure = failure_of(result)
            if failure is None or not session_id:
                return
            from research_core.store import sessions, tasks
            from research_core.tools.base import ToolContext

            ctx = ToolContext.from_env(role, profile_env(IDE_SESSION_ID=session_id))
            session = sessions.read(ctx.wstore, session_id)
            if not session or not session.get("task_id") or ctx.coworld_or_none() is None:
                return
            command = " ".join(rules.texts_in(args))[:300]
            tasks.set_last_error(
                ctx.store,
                ctx.actor,
                session["task_id"],
                {"tool": tool_name, "command": command, **failure},
            )
        except Exception as error:  # best effort only
            log.debug("last-error hook failed: %s", error)

    return {"transform_tool_result": transform_tool_result, "post_tool_call": post_tool_call}
