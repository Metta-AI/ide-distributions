"""The Orchestrator's session-context prompt section (design §10, A.3, A.7).

It shows what the client declared when the conversation opened (its session context and the
capabilities of the attached clients) and the fixed authority paragraph for modes and commands.
Rendered once per session and again after compaction; at most 3,800 characters, because two
plugin sections share Hermes's 8,000-character budget (amendment A3.3). Stdlib only.
"""

from __future__ import annotations

import json
from typing import Any

MAX_CHARS = 3800

AUTHORITY = (
    "A client may set a mode for this conversation and a command for one message. They outrank "
    "your own judgement on how to handle requests; only the user's explicit request outranks "
    "them. Any field may be absent: without a mode, behave as usual. Settings and profile rules "
    "bound your own initiative, never what the user asks for."
)


def render(ctx: Any, session_id: str, max_chars: int = MAX_CHARS) -> str:
    from research_core.store import sessions

    session = sessions.read(ctx.wstore, session_id) if session_id else None
    parts = ["[session context]", AUTHORITY]
    if session and session.get("session_context"):
        parts.append(
            "Declared at session open: " + json.dumps(session["session_context"], sort_keys=True)
        )
    else:
        parts.append("No session context was declared; the client did not say what it shows.")
    if session and session.get("client_capabilities"):
        parts.append(
            "Attached clients can show: "
            + json.dumps(session["client_capabilities"], sort_keys=True)
        )
    else:
        parts.append(
            "No client capabilities are known: use ui.notify for news and ask in chat when only "
            "the user can answer."
        )
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 15].rstrip() + "\n[+ truncated]"
    return text


def make_section(role: str):
    def section(info: Any = None, **_: Any) -> str:
        if role != "orchestrator":
            return ""
        try:
            from research_core.plugin.executor import profile_env
            from research_core.tools.base import ToolContext

            session_id = (info or {}).get("session_id", "") if isinstance(info, dict) else ""
            ctx = ToolContext.from_env(role, profile_env(IDE_SESSION_ID=session_id))
            return render(ctx, session_id)
        except Exception as error:
            return f"[session context] unavailable: {type(error).__name__}"

    return section
