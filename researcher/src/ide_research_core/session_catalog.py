"""The ``capabilities`` tool: searchable profile-configured tools and skills.

STDLIB ONLY at import time: both profiles' plugins load this module inside the Hermes process.
Hermes itself is imported lazily, inside ``hermes_tools`` and ``hermes_skills``.

What it reports, and from where (Hermes v0.21.6):

- Tools: the toolsets Hermes enables for this session's platform
  (``hermes_cli.tools_config._get_platform_tools``, the resolver the gateway, cron and the
  Kanban dispatcher use; ``HERMES_SESSION_PLATFORM``, else ``HERMES_PLATFORM``, else ``cli``,
  which is what a Kanban worker runs as), expanded to tool schemas by
  ``model_tools.get_tool_definitions``. That includes MCP and plugin tools and drops tools whose
  availability check fails. Tools Hermes defers behind ``tool_search`` are the ones missing
  from the assembled (model-visible) list; they are marked ``deferred``.
- Skills: exactly what ``skills_list`` returns (profile skills, ``skills.external_dirs`` such
  as the researcher's installed games, and plugin skills), minus platform-disabled ones.

A tool handler is not told the session's tool list, so this recomputes it from the profile
config. Built-in toolsets the config enables are included, ``memory`` among them (its
availability check follows ``memory.memory_enabled`` / ``user_profile_enabled``). It does not
see tools a session adds on its own outside ``platform_toolsets``: an external memory
provider's or a context engine's tools appended by the agent loop, a per-job
``enabled_toolsets`` override on a cron job, or desktop GUI toolsets.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

DESCRIPTION = (
    "List tools and skills enabled by this profile’s platform configuration, each with a one-line description. "
    "Optionally filter by a keyword. Session-added tools and per-job overrides may be absent; absence does not prove unavailability. Use it to find the right tool or skill before working "
    "around a missing one. Read a skill with skill_view(name); call a tool marked deferred "
    "through tool_call, after tool_describe(name) for its parameters."
)
PARAMETERS = {
    "query": {
        "type": "string",
        "description": "Case-insensitive keyword matched against names, groups and descriptions.",
    },
    "kind": {
        "type": "string",
        "enum": ["tools", "skills"],
        "description": "Only tools or only skills; omit for both.",
    },
}
SUMMARY_CHARACTERS = 160


@dataclass(frozen=True)
class Entry:
    name: str
    group: str  # a tool's toolset, a skill's category
    description: str
    deferred: bool = False

    def matches(self, query: str) -> bool:
        needle = query.casefold()
        return any(needle in text.casefold() for text in (self.name, self.group, self.description))


def summary(text: str) -> str:
    """The first sentence or line of ``text``, at most ``SUMMARY_CHARACTERS`` long."""
    text = " ".join((text or "").split())
    for end in (". ", "? ", "! "):
        position = text.find(end)
        if 0 < position < SUMMARY_CHARACTERS:
            return text[: position + 1]
    return text if len(text) <= SUMMARY_CHARACTERS else text[: SUMMARY_CHARACTERS - 1] + "…"


def session_platform() -> str:
    from gateway.session_context import get_session_env

    platform = get_session_env("HERMES_SESSION_PLATFORM") or os.environ.get("HERMES_PLATFORM")
    return (platform or "cli").strip().lower()


def hermes_tools() -> list[Entry]:
    """The session's tools, from Hermes (see the module docstring)."""
    import model_tools
    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools
    from tools.registry import registry

    enabled = sorted(_get_platform_tools(load_config() or {}, session_platform()))
    # get_tool_definitions records the names it resolves in a process global that execute_code
    # reads as its default sandbox tool list; leave that as the agent loop set it.
    remembered = list(model_tools._last_resolved_tool_names)
    try:
        every = model_tools.get_tool_definitions(
            enabled_toolsets=enabled, quiet_mode=True, skip_tool_search_assembly=True
        )
        visible = model_tools.get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)
    finally:
        model_tools._last_resolved_tool_names = remembered
    visible_names = {definition["function"]["name"] for definition in visible}
    entries = []
    for definition in every:
        function = definition["function"]
        entry = registry.get_entry(function["name"])
        entries.append(
            Entry(
                name=function["name"],
                group=entry.toolset if entry else "other",
                description=summary(function.get("description", "")),
                deferred=function["name"] not in visible_names,
            )
        )
    return entries


def hermes_skills() -> list[Entry]:
    """The session's skills, as ``skills_list`` reports them."""
    from tools.skills_tool import skills_list

    listing = json.loads(skills_list())
    if not listing.get("success", True):
        raise RuntimeError(listing.get("error") or "skills_list failed")
    return [
        Entry(
            name=skill["name"],
            group=skill.get("category") or "general",
            description=summary(skill.get("description", "")),
        )
        for skill in listing.get("skills", [])
    ]


def _grouped(entries: list[Entry], *, tools: bool) -> dict[str, list]:
    groups: dict[str, list] = {}
    for entry in sorted(entries, key=lambda e: (e.group, e.name)):
        line = f"{entry.name}: {entry.description}"
        if tools and entry.deferred:
            line += " [deferred: call through tool_call]"
        groups.setdefault(entry.group, []).append(line)
    return groups


def capabilities(
    query: str | None = None,
    kind: str | None = None,
    *,
    tools: Callable[[], list[Entry]] = hermes_tools,
    skills: Callable[[], list[Entry]] = hermes_skills,
) -> dict:
    """The tool's result: tools grouped by toolset and skills grouped by category."""
    if kind not in (None, "tools", "skills"):
        raise ValueError(f"kind must be 'tools' or 'skills', not {kind!r}")
    query = (query or "").strip()
    result: dict = {"query": query or None}
    for name, source in (("tools", tools), ("skills", skills)):
        if kind not in (None, name):
            continue
        entries = [entry for entry in source() if not query or entry.matches(query)]
        result[name] = _grouped(entries, tools=name == "tools")
        result[f"{name}_count"] = len(entries)
    result["read_more"] = (
        "skill_view(name) loads a skill; tool_describe(name) shows a deferred tool's parameters "
        "and tool_call runs it; other tools are called directly."
    )
    return result


def handle(args: dict, **_) -> str:
    """Hermes tool handler: JSON text, with failures reported rather than raised."""
    try:
        return json.dumps(capabilities(args.get("query"), args.get("kind")), ensure_ascii=False)
    except Exception as error:  # noqa: BLE001 - tool boundary reports Hermes failures
        return json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"})


def register(ctx, toolset: str) -> None:
    """Register ``capabilities`` in ``toolset`` (each profile's own plugin toolset)."""
    ctx.register_tool(
        name="capabilities",
        toolset=toolset,
        schema={
            "name": "capabilities",
            "description": DESCRIPTION,
            "parameters": {"type": "object", "properties": PARAMETERS, "required": []},
        },
        handler=handle,
        description=DESCRIPTION,
    )
