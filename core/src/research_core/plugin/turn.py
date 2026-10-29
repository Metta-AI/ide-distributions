"""The Orchestrator's chat-turn hook (design A.4, H "Notices and continuity").

Before each model call in a chat Session, Hermes's ``pre_llm_call`` hook adds to the user's
message: the validated client context block when the message carries one, the unread notices
(the only channel background work has to the user), a short note of what changed in the store
since this conversation's last turn, the active Coworld, and, on the first turn of a new chat
after an hour away, a summary of what happened meanwhile. Wake and task Sessions get nothing.
Stdlib only: this runs inside Hermes and must never fail a turn.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from research_core import contract
from research_core.tools.catalog import Role

log = logging.getLogger(__name__)

AWAY_AFTER = timedelta(hours=1)
MAX_NOTICES = 12
MAX_CHANGED_KINDS = 10
SUMMARY_KINDS = {
    "task.completed": "Tasks completed",
    "task.failed": "Tasks failed",
    "task.merged": "Merged into main",
    "version.submitted": "Submissions",
    "version.uploaded": "Uploads",
    "task.asked": "Researcher questions",
    "task.proposed": "Proposed Tasks",
    "task.proposed_submission": "Proposed submissions",
    "goal.park": "Goals parked",
    "capability.failed": "Broken capabilities",
    "champion.changed": "Champion changes",
}


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def render_context_block(user_message: str) -> str | None:
    """The validated context block as a short labelled rendering, or a one-line problem."""
    parsed, error = contract.find_block(user_message or "")
    if parsed is None and error is None:
        return None
    if error:
        return f"[client context] {error}; the message was handled without it."
    kept, problems = contract.validate_context(parsed)
    lines = []
    if kept.get("mode"):
        mode = kept["mode"]
        lines.append(
            f"mode: {mode.get('name')}"
            + (f" — {mode['instruction']}" if mode.get("instruction") else "")
        )
    if kept.get("command"):
        command = kept["command"]
        lines.append(
            f"command (this message only): {command.get('name')}"
            + (f" — {command['instruction']}" if command.get("instruction") else "")
        )
    if kept.get("client"):
        client = kept["client"]
        bits = [client.get("kind") or "", client.get("id") or ""]
        lines.append(
            "client: "
            + " ".join(b for b in bits if b).strip()
            + (f" (guide skill: {client['guide']})" if client.get("guide") else "")
        )
    for key in ("league", "coworld", "opening_goal", "parent_session"):
        if kept.get(key):
            lines.append(f"{key}: {kept[key]}")
    if kept.get("policy"):
        lines.append(f"policy: {kept['policy']}")
    if kept.get("view"):
        view = kept["view"]
        lines.append(
            "view: "
            + ", ".join(str(view[k]) for k in ("id", "label", "description") if view.get(k))
        )
    if kept.get("selection"):
        lines.append(f"selection: {kept['selection']}")
    if kept.get("references"):
        lines.append(f"references: {kept['references']}")
    if kept.get("on_screen"):
        lines.append(f"on screen: {', '.join(kept['on_screen'])}")
    if kept.get("replay_moment"):
        lines.append(f"replay moment: {kept['replay_moment']}")
    if kept.get("voice"):
        lines.append(f"voice: {kept['voice']}")
    if kept.get("client_data"):
        lines.append(f"client data (not interpreted): {kept['client_data']}")
    if problems:
        lines.append("ignored: " + "; ".join(problems))
    return "[client context] " + ("\n".join(lines) if lines else "empty")


def render_turn(ctx: Any, session_id: str, *, is_first_turn: bool, now: datetime) -> str:
    """Notices, the changed-since note, the active Coworld and the away summary, as text."""
    from research_core.store import coworlds, notices

    wstore = ctx.wstore
    parts: list[str] = []

    last = wstore.one(
        "SELECT seq, at, payload FROM events WHERE kind = 'turn.rendered' AND subject = ?"
        " ORDER BY seq DESC LIMIT 1",
        (session_id,),
    )
    last_any = wstore.one(
        "SELECT at FROM events WHERE kind = 'turn.rendered' ORDER BY seq DESC LIMIT 1"
    )
    import json

    last_payload = json.loads(last["payload"]) if last else {}
    since_w = int(last_payload.get("wseq") or 0)
    since_c = int(last_payload.get("cseq") or 0)

    active = coworlds.active(wstore)
    coworld_store = None
    if active is not None:
        try:
            coworld_store = ctx.store
        except Exception:
            coworld_store = None

    unread = notices.unread(wstore, limit=MAX_NOTICES)
    if unread:
        lines = [
            f"- {n['text']}" + (f" ({', '.join(n['refs'])})" if n.get("refs") else "")
            for n in unread
        ]
        parts.append("[notices since your last turn]\n" + "\n".join(lines))
        ids = [int(str(n["id"]).split("-")[-1]) for n in unread]
        notices.mark_read(wstore, ctx.actor, ids, by=f"session:{session_id}")

    changed: Counter[str] = Counter()
    for store, since in ((wstore, since_w), (coworld_store, since_c)):
        if store is None:
            continue
        for event in store.events_after(since, limit=500):
            if event.kind in ("turn.rendered", "watcher.heartbeat", "notice", "ui.patch"):
                continue
            changed[event.kind] += 1
    if last is not None and changed:
        top = ", ".join(f"{kind} ×{n}" for kind, n in changed.most_common(MAX_CHANGED_KINDS))
        parts.append(f"[changed since your last turn] {top}")

    away_from = _parse_time(last_any["at"]) if last_any else None
    if is_first_turn and last is None and away_from is not None and now - away_from >= AWAY_AFTER:
        summary = _away_summary(wstore, coworld_store, away_from, now)
        if summary:
            parts.append(summary)

    if active is not None:
        parts.append(f"[active Coworld] {active['coworld_id']}")
    else:
        parts.append("[active Coworld] none yet; workspace.coworld_switch selects one")

    wseq, cseq = wstore.last_seq(), (coworld_store.last_seq() if coworld_store else 0)
    with wstore.tx(ctx.actor) as tx:
        tx.event("turn.rendered", session_id, {"wseq": wseq, "cseq": cseq, "first": is_first_turn})
    return "\n\n".join(parts)


def _away_summary(wstore: Any, coworld_store: Any, since: datetime, now: datetime) -> str:
    counts: Counter[str] = Counter()
    for store in (wstore, coworld_store):
        if store is None:
            continue
        rows = store.q(
            "SELECT kind FROM events WHERE at >= ?", (since.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),)
        )
        for row in rows:
            if row["kind"] in SUMMARY_KINDS:
                counts[row["kind"]] += 1
    if not counts:
        return ""
    hours = max(1, int((now - since).total_seconds() // 3600))
    lines = [f"- {SUMMARY_KINDS[kind]}: {n}" for kind, n in counts.most_common()]
    return (
        f"[while you were away, about {hours} h] open with a short summary of this:\n"
        + "\n".join(lines)
    )


def make_pre_llm_call(role: Role) -> Callable[..., dict | None]:
    def pre_llm_call(
        session_id: str = "", user_message: str = "", is_first_turn: bool = False, **_: Any
    ) -> dict | None:
        if role != "orchestrator" or not session_id:
            return None
        try:
            from research_core.store import sessions
            from research_core.tools.base import ToolContext

            ctx = ToolContext.from_env(role, dict(os.environ, IDE_SESSION_ID=session_id))
            session = sessions.read(ctx.wstore, session_id)
            if session is not None and session.get("kind") != "chat":
                return None
            if session is None:
                sessions.register(
                    ctx.wstore,
                    ctx.actor,
                    session_id=session_id,
                    profile=os.environ.get("HERMES_PROFILE_NAME") or "orchestrator",
                    kind="chat",
                )
            parts = []
            block = render_context_block(user_message)
            if block:
                parts.append(block)
            parts.append(
                render_turn(ctx, session_id, is_first_turn=is_first_turn, now=datetime.now(UTC))
            )
            return {"context": "\n\n".join(p for p in parts if p)}
        except Exception as error:  # never fail a turn
            log.debug("turn hook failed: %s", error)
            return None

    return pre_llm_call
