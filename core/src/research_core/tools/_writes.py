"""Shared rules of the forum and wiki write tools (design B.9, C.2, H settings).

A platform write setting (``platform_writes.forum`` / ``platform_writes.wiki``) bounds only the
agent's own initiative: ``allowed`` writes, ``ask`` has the Orchestrator ask the user first and a
Researcher propose through its Task, ``never`` stops writes the agent would make on its own. A
write the user asked for (``cites`` names the message) always runs. Every write records an intent
first and reconciles before a retry (B.13); a write with the User identity leaves a notice.
"""

from __future__ import annotations

import hashlib
from typing import Any

from research_core.store import intents, notices, settings
from research_core.tools.base import ToolCall, ToolError


def setting_allows(call: ToolCall, key: str) -> None:
    """Refuse an agent-initiated write the setting stops; a cited user request always passes."""
    ctx = call.ctx
    if call.get("cites"):
        return
    value = settings.get((ctx.wstore, ctx.store), key)
    if value == "allowed":
        return
    if value == "ask":
        if ctx.role == "orchestrator":
            raise ToolError(
                f"{key} is 'ask': ask the user first (ui.ask), then call again with cites naming "
                "their answer",
                ask_user=True,
                setting=key,
            )
        raise ToolError(
            f"{key} is 'ask': propose the post through your Task (task.ask or the result of "
            "task.complete) and let the Orchestrator decide",
            ask_orchestrator=True,
            setting=key,
        )
    raise ToolError(
        f"{key} is 'never' for the agents' own initiative; a user request overrides it: call "
        "again with cites naming their message",
        setting=key,
    )


def intent_number(record: dict[str, Any]) -> int:
    value = record["id"]
    return int(str(value).split("-")[-1])


def begin_intent(call: ToolCall, *, kind: str, key: str, subject: str | None) -> dict[str, Any]:
    return intents.begin(call.ctx.store, call.ctx.actor, kind=kind, key=key, subject=subject)


def finish_intent(call: ToolCall, intent: dict[str, Any], *, state: str, result: dict) -> None:
    intents.finish(
        call.ctx.store, call.ctx.actor, intent_number(intent), state=state, result=result
    )


def digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def notice_user_write(call: ToolCall, identity: str, what: str) -> None:
    if identity == "user":
        notices.add(call.ctx.wstore, call.ctx.actor, text=f"Acted as your User identity: {what}")
