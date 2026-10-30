"""The ``goal`` tool (design C.2): what the user wants, as a record the briefing shows."""

from __future__ import annotations

from research_core.store import goals
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
BOTH = frozenset({"orchestrator", "researcher"})

GOAL = a.string("The Goal, such as G-3.")


def handle(call: ToolCall) -> dict:
    store, actor = call.ctx.store, call.ctx.actor
    if call.action == "create":
        record = goals.create(
            store,
            actor,
            title=call.get("title"),
            end_state=call.get("end_state"),
            reason=call.get("reason"),
            description=call.get("description") or "",
            judgement=call.get("judgement"),
            tags=tuple(call.get("tags") or ()),
        )
        return {"record": record}
    goal_id = a.record_number(call.get("goal"), "goal", "goal")
    if call.action == "update":
        fields = {
            name: call.get(name) for name in goals.UPDATABLE_FIELDS if call.get(name) is not None
        }
        return {"record": goals.update(store, actor, goal_id, **fields)}
    if call.action == "set_status":
        record = goals.set_status(
            store, actor, goal_id, call.get("status"), call.get("status_reason") or ""
        )
        return {"record": record}
    if call.action == "read":
        return {"record": goals.read(store, goal_id)}
    raise AssertionError(call.action)


def handle_list(call: ToolCall) -> dict:
    return {"records": goals.list(call.ctx.store, status=call.get("status"))}


def dispatch(call: ToolCall) -> dict:
    if call.action == "list":
        return handle_list(call)
    return handle(call)


SPEC = tool(
    ToolSpec(
        name="goal",
        description=(
            "Goals: what the user wants, with an end state the work is judged by. Goals and "
            "Tasks are the plan; there is no plan file. Returns the updated record."
        ),
        handler=dispatch,
        roles=BOTH,
        executor="inproc",
        actions=(
            ActionSpec(
                "create",
                "Create a Goal from the user's request.",
                {
                    "title": a.string("Short name of the outcome."),
                    "end_state": a.string("What must be true for the Goal to be complete."),
                    "reason": a.string("Why the Goal exists, usually quoting the user."),
                    "description": a.string("Detail, in Markdown."),
                    "judgement": a.string("How to judge the end state."),
                    "tags": a.strings("Keywords from the shared vocabulary."),
                },
                ("title", "end_state", "reason"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "update",
                "Change a Goal's fields, its 'where it stands' line or add an update for the user.",
                {
                    "goal": GOAL,
                    "title": a.string("New title."),
                    "end_state": a.string("New end state."),
                    "reason": a.string("New reason."),
                    "description": a.string("New description."),
                    "judgement": a.string("New judgement instructions."),
                    "tags": a.strings("New tags (replaces the list)."),
                    "where_it_stands": a.string("One line on where the Goal stands now."),
                    "update_for_human": a.string("A plain-language update appended for the user."),
                },
                ("goal",),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "set_status",
                "Park, resume, complete or abandon a Goal; every change needs a reason.",
                {
                    "goal": GOAL,
                    "status": a.string(
                        "active, parked, completed or abandoned.",
                        enum=["active", "parked", "completed", "abandoned"],
                    ),
                    "status_reason": a.string("Why the status changes."),
                },
                ("goal", "status", "status_reason"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "read", "Read a Goal with its computed progress.", {"goal": GOAL}, ("goal",)
            ),
            ActionSpec(
                "list",
                "List Goals, optionally by status.",
                {"status": a.string("Filter: active, parked, completed or abandoned.")},
            ),
        ),
    )
)
