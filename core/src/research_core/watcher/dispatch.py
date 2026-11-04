"""Read-only worker inventory and task prompt rendering. Coordinator owns scheduling."""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any

from research_core.store import goals, sessions
from research_core.watcher.resume import is_live, run_status

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store
    from research_core.watcher.context import TickContext

WAKES_DIR = Path(__file__).with_name("wakes")


def render(name: str, **values: Any) -> str:
    """A prompt from ``wakes/<name>.md`` with ``$name`` placeholders filled in."""
    template = Template((WAKES_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return template.safe_substitute({k: "none" if v is None else str(v) for k, v in values.items()})


def live_task_sessions(ctx: TickContext) -> dict[str, Any]:
    """Task Sessions whose run the gateway reports as not terminal: ``{session_id: status}``."""
    rows = ctx.wstore.q(
        "SELECT id FROM sessions WHERE kind = 'task' AND run_id IS NOT NULL AND ended IS NULL"
        " ORDER BY started, id"
    )
    live: dict[str, Any] = {}
    for row in rows:
        session = sessions.read(ctx.wstore, row["id"])
        status = run_status(ctx, session)
        if is_live(status):
            live[row["id"]] = status
    return live


def drained_sessions(ctx: TickContext) -> list[dict[str, Any]]:
    """Task Sessions of the active Coworld that a drain stopped, oldest first."""
    rows = ctx.wstore.q(
        "SELECT id FROM sessions WHERE drained = 1 AND kind = 'task' AND coworld_id = ?"
        " ORDER BY started, id",
        (ctx.coworld.coworld_id,),
    )
    return [sessions.read(ctx.wstore, row["id"]) for row in rows]


def start_text(store: Store, coworld, task: dict[str, Any]) -> str:
    """The ``task_start`` prompt for a Task: the record, its Goal, worktree and first call."""
    n = int(task["id"].removeprefix("T-"))
    experiment = f"X-{task['experiment_id']}" if task["experiment_id"] else None
    end_condition = task["end_condition"]
    if end_condition and task["end_detail"]:
        end_condition = f"{end_condition}: {task['end_detail']}"
    return render(
        "task_start",
        coworld=coworld.coworld_id,
        task_id=task["id"],
        title=task["title"],
        type=task["type"] + (f" (repair {task['capability']})" if task["capability"] else ""),
        priority=task["priority"],
        goal=_goal_line(store, task["goal_id"]),
        end_condition=end_condition,
        experiment=experiment,
        depends_on=", ".join(task["depends_on"]) or None,
        description=task["description"],
        reasoning=task["reasoning"] or "",
        worktree=coworld.worktree_for_task(n),
        branch=f"task/{n}",
    )


def _goal_line(store: Store, goal_id: int | None) -> str:
    if goal_id is None:
        return "workspace maintenance (no Goal)"
    goal = goals.read(store, goal_id)
    return f"{goal['id']} {goal['title']} — end state: {goal['end_state']}"
