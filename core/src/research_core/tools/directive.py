"""The ``directive`` tool (design B.14): a standing user instruction the briefing always shows."""

from __future__ import annotations

from research_core.store import directives
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
        return {"record": record}
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
        return {"record": record}
    if call.action == "retire":
        return {"record": directives.retire(store, actor, directive_id)}
    raise AssertionError(call.action)


SPEC = tool(
    ToolSpec(
        name="directive",
        description=(
            "Directives: standing user instructions, word for word, on the workspace or on one "
            "Goal. The briefing shows every active directive, so compaction cannot lose them."
        ),
        handler=handle,
        roles=BOTH,
        executor="inproc",
        actions=(
            ActionSpec(
                "add",
                "Record a standing instruction the user gave.",
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
