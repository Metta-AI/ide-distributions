"""The ``directive`` tool (design B.14): a standing user instruction.

A Session sees a directive in its briefing only if the directive was active when that Session's
prompt was written, or through ``directive.list``. A Researcher already running sees neither on its
own, so ``add`` and ``update`` name the Tasks in progress, and the Orchestrator tells them with
``task.message``.
"""

from __future__ import annotations

from research_core.store import directives, tasks
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
BOTH = frozenset({"orchestrator", "researcher"})
DIRECTIVE = a.string("The directive: W-2 on the workspace, or D-2 on a Goal.")
TEXT = a.string("The instruction, in the user's words.")
SCOPE = a.string(
    "'workspace' or a Goal such as G-3; add defaults to workspace, list without it shows all."
)


def normalise_scope(ctx: ToolContext, scope: str | None) -> str:
    if scope in (None, "", "workspace"):
        return "workspace"
    try:
        goal_id = a.record_number(scope.removeprefix("goal:"), "goal", "scope")
    except ToolError as error:
        raise ToolError(f"scope must be 'workspace' or a Goal such as G-3: {error}") from None
    return f"goal:{goal_id}"


def store_for(ctx: ToolContext, scope: str):
    return ctx.wstore if scope == "workspace" else ctx.store


def running_tasks_note(task_ids: list[str]) -> str:
    return (
        f"{', '.join(task_ids)} in progress: its Researcher does not see this until you send "
        "task.message {task, text}. A Task started later gets it in its briefing."
    )


def with_running_tasks_note(ctx: ToolContext, scope: str, result: dict) -> dict:
    """Add a ``next`` naming the Tasks in progress that the directive covers, if there are any."""
    if ctx.coworld_or_none() is None:
        return result
    goal_id = None if scope == "workspace" else int(scope.removeprefix("goal:"))
    running = tasks.list(ctx.store, status="in-progress", goal_id=goal_id)
    if running:
        result["next"] = running_tasks_note([task["id"] for task in running])
    return result


def handle(call: ToolCall) -> dict:
    ctx, actor = call.ctx, call.ctx.actor
    if call.action == "add":
        scope = normalise_scope(ctx, call.get("scope"))
        record = directives.add(
            store_for(ctx, scope),
            actor,
            text=call.get("text"),
            scope=scope,
            source=call.get("source"),
        )
        return with_running_tasks_note(ctx, scope, {"record": record})
    if call.action == "list":
        scope = call.get("scope")
        records = []
        if scope in (None, "", "workspace"):
            records += directives.list(
                ctx.wstore, scope="workspace", status=call.get("status") or "active"
            )
        if scope not in ("workspace",) and ctx.coworld_or_none() is not None:
            goal_scope = normalise_scope(ctx, scope) if scope else None
            records += directives.list(
                ctx.store, scope=goal_scope, status=call.get("status") or "active"
            )
        return {"records": records}
    home, directive_id = directives.parse_reference(call.get("directive"))
    store = ctx.wstore if home == "workspace" else ctx.store
    if call.action == "update":
        new_scope = normalise_scope(ctx, call.get("scope")) if call.get("scope") else None
        record = directives.update(
            store, actor, directive_id, text=call.get("text"), scope=new_scope
        )
        return with_running_tasks_note(ctx, new_scope or record["scope"], {"record": record})
    if call.action == "retire":
        return {"record": directives.retire(store, actor, directive_id)}
    raise AssertionError(call.action)


SPEC = tool(
    ToolSpec(
        name="directive",
        description=(
            "Directives: standing user instructions, word for word, on the workspace or on one "
            "Goal. They are stored, so a summary cannot lose them. A Session's briefing shows "
            "those active when it was written; list shows the current ones."
        ),
        handler=handle,
        roles=BOTH,
        executor="inproc",
        actions=(
            ActionSpec(
                "add",
                "Record a standing instruction or preference the user gave.",
                {
                    "text": TEXT,
                    "scope": SCOPE,
                    "source": a.string("Where the user said it: a message reference or quote."),
                },
                ("text", "source"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "update",
                "Change a directive's text or scope.",
                {"directive": DIRECTIVE, "text": TEXT, "scope": SCOPE},
                ("directive",),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "retire",
                "Retire a directive the user withdrew.",
                {"directive": DIRECTIVE},
                ("directive",),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "list",
                "List active directives for the workspace and the active Coworld's Goals.",
                {
                    "scope": SCOPE,
                    "status": a.string("Default active.", enum=["active", "retired"]),
                },
            ),
        ),
    )
)
