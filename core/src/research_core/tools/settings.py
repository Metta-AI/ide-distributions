"""The ``settings`` tool (design B.9, Appendix H): bounds on the agents' own initiative.

Each key has a scope (A2.4); ``update`` writes to the store that scope names, so the caller
never picks a store. Settings never refuse a user's explicit request, and an update that
reflects a user preference should also become a preference claim: the result says so.
"""

from __future__ import annotations

from typing import Any

from research_core.store import settings
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
BOTH = frozenset({"orchestrator", "researcher"})

NEXT = (
    "record a preference claim (claim.write tagged preference) when this reflects a user preference"
)


def describe(found: settings.SettingSpec, value: Any) -> dict:
    """One setting as the agent sees it: the value and the spec that explains it."""
    record = {
        "key": found.key,
        "value": value,
        "type": found.type,
        "default": found.default,
        "scope": found.scope,
        "enforced_by": found.enforced_by,
        "description": found.description,
        "nullable": found.nullable,
    }
    if found.enum is not None:
        record["enum"] = list(found.enum)
    return record


def stores_available(ctx: ToolContext) -> list:
    """The workspace store, plus the Coworld store when a Coworld is active."""
    stores = [ctx.wstore]
    if ctx.coworld_or_none() is not None:
        stores.append(ctx.store)
    return stores


def store_for(ctx: ToolContext, found: settings.SettingSpec):
    if found.scope == "workspace":
        return ctx.wstore
    try:
        return ctx.store
    except ToolError as error:
        raise ToolError(f"{found.key} is a coworld setting: {error}") from None


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    if call.action == "read":
        stores = stores_available(ctx)
        key = call.get("key")
        if key:
            return {"record": describe(settings.spec(key), settings.get(stores, key))}
        kinds = {store.kind for store in stores}
        return {
            "records": [
                describe(found, settings.get(stores, found.key))
                for found in settings.SETTINGS.values()
                if found.scope in kinds
            ]
        }
    if call.action == "update":
        found = settings.spec(call.get("key"))
        record = settings.update(
            store_for(ctx, found),
            ctx.actor,
            found.key,
            call.get("value"),
            cites=call.get("cites"),
        )
        return {"record": record, "next": NEXT}
    raise AssertionError(call.action)


SPEC = tool(
    ToolSpec(
        name="settings",
        description=(
            "Settings bound the agents' own initiative (pauses, researcher count, approval "
            "gates, submission rules); they never refuse what the user asks for. Returns the "
            "setting with its spec."
        ),
        handler=handle,
        roles=BOTH,
        executor="inproc",
        actions=(
            ActionSpec(
                "read",
                "Read one setting, or all of them with their types, defaults and scopes.",
                {"key": a.string("The setting, such as paused; omit for all.")},
            ),
            ActionSpec(
                "update",
                "Change a setting at the user's request; cites names the message that asked.",
                {
                    "key": a.string("The setting, such as max_researchers."),
                    "value": {
                        "description": "The new value in the setting's type; null clears a "
                        "nullable setting."
                    },
                    "cites": a.string("The user's message that asked for the change."),
                },
                ("key", "cites"),
                ORCHESTRATOR,
            ),
        ),
    )
)
