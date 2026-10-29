"""Step 12b: the computed part of the ``agent-context`` panel (design A.13, amendment A3.5).

The panel lives in the UI state of the fixed conversation ``workspace`` at
``/panels/agent-context`` as ``{"kind": "agent-context", "content": {"computed", "needs"}}``.
The watcher computes the workspace part from records with no model turn: the active Coworld,
the image, the acting Player and whether the User identity is available, Goals (goal-less
maintenance work shows as "workspace maintenance", A2.2), the champion rows, the last Versions
with their submission status as the record knows it, the running Tasks with their Sessions and
runs, the capabilities with their repair Tasks, and the watcher's own state. ``needs`` ("I need
from you") is the agent's and is carried over untouched.

The document is patched only when the computed part differs from what is stored, so a quiet
workspace writes no ``ui.patch`` event. For that reason the heartbeat appears as "alive" and
"has work" rather than as a timestamp that changes every tick; the bridge reads the exact
heartbeat from ``meta``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from research_core import environment_guide
from research_core.store import (
    capabilities,
    champion,
    coworlds,
    settings,
    tasks,
    ui_state,
    versions,
)
from research_core.store import goals as goals_store
from research_core.store._common import parse_iso
from research_core.store.db import Store

if TYPE_CHECKING:  # pragma: no cover
    from research_core.watcher.context import TickContext

CONVERSATION = "workspace"
PANEL = "agent-context"
PANEL_PATH = f"/panels/{PANEL}"
VERSION_LIMIT = 5
HEARTBEAT_ALIVE = timedelta(minutes=3)
COWORLD_FIELDS = ("coworld_id", "folder", "mixin_version", "tool_check_status", "set_up_at")
GOAL_STATUSES_SHOWN = ("active", "parked")


def run(ctx: TickContext) -> dict[str, Any]:
    computed = compute(ctx)
    return {"written": write(ctx, computed)}


def compute(ctx: TickContext) -> dict[str, Any]:
    """The workspace part of the panel, as JSON-ready data."""
    env = ctx.env
    active = coworlds.active(ctx.wstore)
    heartbeat = ctx.meta("heartbeat")
    alive = heartbeat is not None and ctx.now - parse_iso(heartbeat) <= HEARTBEAT_ALIVE
    computed: dict[str, Any] = {
        "coworld": None if active is None else {k: active[k] for k in COWORLD_FIELDS},
        "image": environment_guide.image_info(env),
        "player": env.get("SOFTMAX_PLAYER_ID"),
        "user_identity_available": bool(env.get("SOFTMAX_USER_CREDENTIAL")),
        "paused": bool(settings.get(ctx.wstore, "paused")),
        "heartbeat": {"alive": alive, "has_work": ctx.meta("has_work") == "1"},
        "goals": [],
        "champion": [],
        "versions": [],
        "running_tasks": [],
        "capabilities": [],
    }
    if ctx.store is not None:
        computed.update(_coworld_part(ctx.store))
    return json.loads(json.dumps(computed, default=str))


def write(ctx: TickContext, computed: dict[str, Any]) -> bool:
    """Patch the panel when ``computed`` differs from the stored part; keep ``needs``."""
    doc = ui_state.get(ctx.wstore, CONVERSATION)["doc"]
    panels = doc.get("panels") if isinstance(doc.get("panels"), dict) else None
    current = panels.get(PANEL) if panels is not None else None
    content = current.get("content") if isinstance(current, dict) else None
    content = content if isinstance(content, dict) else {}
    if content.get("computed") == computed:
        return False
    value = {"kind": PANEL, "content": {"computed": computed, "needs": content.get("needs", [])}}
    if panels is None:
        ops = [{"op": "add", "path": "/panels", "value": {PANEL: value}}]
    elif current is None:
        ops = [{"op": "add", "path": PANEL_PATH, "value": value}]
    else:
        ops = [{"op": "replace", "path": PANEL_PATH, "value": value}]
    ui_state.patch(ctx.wstore, ctx.actor, CONVERSATION, ops)
    return True


# --- the Coworld's part ------------------------------------------------------------------------


def _coworld_part(store: Store) -> dict[str, Any]:
    return {
        "goals": _goals(store),
        "champion": _champion_rows(store),
        "versions": [
            {
                "id": record["id"],
                "summary": record["summary"],
                "submission_status": record["submission_status"],
                "created": record["created"],
            }
            for record in versions.list(store, VERSION_LIMIT)
        ],
        "running_tasks": [
            {
                "id": task["id"],
                "title": task["title"],
                "type": task["type"],
                "goal": None if task["goal_id"] is None else f"G-{task['goal_id']}",
                "session": task["held_by"],
                "run": task["run_id"],
                "waiting_on": (task["waiting_on"] or {}).get("kind"),
            }
            for task in tasks.list(store, status="in-progress")
        ],
        "capabilities": [
            {
                "name": row["name"],
                "status": row["status"],
                "maintenance_task": _task_ref(row["maintenance_task_id"]),
                "last_check": row["last_check"],
            }
            for row in capabilities.status(store)
        ],
    }


def _goals(store: Store) -> list[dict[str, Any]]:
    shown = [
        {"id": goal["id"], "title": goal["title"], "status": goal["status"]}
        for goal in goals_store.list(store)
        if goal["status"] in GOAL_STATUSES_SHOWN
    ]
    maintenance = goals_store.read(store, None)
    if maintenance["tasks"]["open"]:
        shown.append(
            {
                "id": None,
                "title": maintenance["title"],
                "status": maintenance["status"],
                "open_tasks": maintenance["tasks"]["open"],
            }
        )
    return shown


def _champion_rows(store: Store) -> list[dict[str, Any]]:
    rows = []
    for row in store.q("SELECT league_id FROM champion ORDER BY league_id"):
        record = champion.read(store, row["league_id"])
        rows.append(
            {
                "league_id": record["league_id"],
                "state": record["state"],
                "policy_version_id": record["policy_version_id"],
                "version": None if record["version_id"] is None else f"V-{record['version_id']}",
                "since": record["since"],
            }
        )
    return rows


def _task_ref(task_id: int | None) -> str | None:
    return None if task_id is None else f"T-{task_id}"
