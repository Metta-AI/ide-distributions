"""What a session of each role sees: the system prompt, the skill index and the tool list.

The usability measurement (``test_usability.py``) tests the model on the real way of finding
things, so these are built from the shipped sources every run and a later run sees any change to
the tools, prompts or skills:

- the SOUL comes from ``prompts.build.render``;
- the skill index is rendered from the real ``SKILL.md`` files the way Hermes v0.22.6 renders it
  (``agent/prompt_builder.py`` ``_render_skills_index``). On 2026-11-04 the output was byte for
  byte what ``build_skills_system_prompt`` returned in the pinned image, for both roles;
- the IDE tools come from ``harness.SchemaSet``, run through Hermes's own sanitizers with
  ``harness.hermes_sanitizers``;
- ``render_context_block``, the wake templates and the task start template are the real ones.

``usability_hermes.json`` holds what is Hermes's, as the pinned image (v0.22.6) gives it: the
prompt sections around the skill index (from a request captured on 2026-11-03) and the built-in
tools. The tools are ``model_tools.get_tool_definitions(enabled_toolsets=["hermes-api-server"],
disabled_toolsets=["browser", "connections", "cronjob"])`` run inside the image, without
``memory`` (the profiles turn it off) and with ``vision_analyze`` (it appears only with a vision
provider; its schema is from the same capture). ``delegate_task`` has the text a real session
froze, which leaves out the sentence on what children may call. ``hermes_builtin_tools.json`` on
``main`` is still v0.21.6, with other ``terminal`` and ``skill_view`` descriptions; the
``tools/model-test-gaps`` branch refreshes it. The ``Execution discipline`` section is the one Hermes adds only for Kimi models, so it
is here for both roles because the test model is Kimi.

What is not real: the workspace itself. :data:`BRIEFING` and the session context describe a made
up Coworld, in the format the plugin renders, and no tool touches a store;
``usability_reads`` answers for them.
"""

# ruff: noqa: E501 (the briefing below is one line per item, as the plugin renders it)
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

import research_core
from research_core.plugin.session_context import AUTHORITY, OPENING_GOAL, no_client_text
from research_core.prompts import build
from research_core.tools.catalog import Role
from tests.model import harness

HERMES = json.loads(Path(__file__).with_name("usability_hermes.json").read_text(encoding="utf-8"))
SKILLS_DIR = Path(research_core.__file__).parent / "skills"
COWORLD = "cow_cogs"
TASK_ID = "T-21"
COWORLD_ROOT = f"/opt/data/workspace/{COWORLD}"
COWORLD_CODE = f"{COWORLD_ROOT}/code"
BINDINGS = ("ab", "diagnosis", "eval-design", "replay-inspection", "survey")


# --- skills -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    owner: str  # common, orchestrator or researcher
    category: str  # the folder between the owner and the skill; a top-level skill is its own
    path: Path
    text: str  # the whole file, as skill_view returns it


@functools.cache
def all_skills() -> tuple[Skill, ...]:
    found = []
    for path in sorted(SKILLS_DIR.glob("*/**/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        front = yaml.safe_load(text.split("---", 2)[1])
        relative = path.relative_to(SKILLS_DIR).parts  # owner, [category], skill, SKILL.md
        category = relative[1] if len(relative) == 4 else front["name"]
        found.append(Skill(front["name"], front["description"], relative[0], category, path, text))
    return tuple(found)


def skills_for(role: Role) -> list[Skill]:
    return [skill for skill in all_skills() if skill.owner in ("common", role)]


def skill_index(role: Role) -> str:
    """The ``<available_skills>`` block, ordered and indented as Hermes does."""
    by_category: dict[str, list[Skill]] = {}
    for skill in skills_for(role):
        by_category.setdefault(skill.category, []).append(skill)
    lines = []
    for category in sorted(by_category):
        rows = sorted(by_category[category], key=lambda skill: skill.name)
        indent = "  " if len(rows) == 1 else "    "
        if len(rows) > 1:
            lines.append(f"  {category}:")
        lines.extend(f"{indent}- {row.name}: {row.description}" for row in rows)
    return "<available_skills>\n" + "\n".join(lines) + "\n</available_skills>"


def bare_skill_name(name: str) -> str:
    """``method/scouting`` and ``method:scouting`` are the same skill as ``scouting``: Hermes
    tries the path under each skill folder, and reads ``a:b`` with no plugin ``a`` as ``a/b``."""
    return name.replace(":", "/").rsplit("/", 1)[-1]


def skill_view_result(role: Role, name: str) -> str:
    """What ``skill_view`` returns: Hermes's result shape, or its refusal for an unknown skill."""
    for skill in skills_for(role):
        if skill.name == bare_skill_name(name):
            return json.dumps(
                {
                    "success": True,
                    "name": skill.name,
                    "description": skill.description,
                    "tags": [],
                    "related_skills": [],
                    "content": skill.text,
                    "path": f"{skill.name}/SKILL.md",
                    "linked_files": None,
                },
                ensure_ascii=False,
            )
    available = [skill.name for skill in skills_for(role)]
    return json.dumps(
        {"success": False, "error": f"Skill '{name}' not found.", "available_skills": available}
    )


def skills_list_result(role: Role) -> str:
    rows = [
        {"name": skill.name, "description": skill.description, "category": skill.category}
        for skill in skills_for(role)
    ]
    return json.dumps({"success": True, "skills": rows, "count": len(rows)})


# --- the system prompt ------------------------------------------------------------------------

# A made-up workspace in the format ``plugin/briefing.py`` renders. The ids here are the ones the
# situations name, so a model reading the briefing finds the records a situation talks about.
BRIEFING_HEADING = (
    "[briefing] Written 2026-11-04 18:00 UTC and not updated in this Session: read the record when "
    "the current state matters."
)
BRIEFING = (
    BRIEFING_HEADING
    + """
Workspace: Coworld cow_cogs; paused: no; watcher heartbeat just now; credits: 840.0
Your Player: id ply_cogs
Capability check: 4 minutes ago; 7 of 7 ok
User identity: available
Directives (2 active, the user's standing instructions):
- W-1 (workspace): "keep replies short"
- D-3 (G-1): "never touch the opening book"
Champion in league_cogs_main: V-7 (competing since 2026-10-30)
V-12 built: "Lower retreat threshold" (2026-11-04)
V-11 uploaded: "Retreat when outnumbered" (2026-11-03)
V-7 champion: "Baseline with resource cap fix" (2026-10-28)
{your_task}Active Goals (2):
- G-1 "Reach the top 3 of league_cogs_main": rank 5 after round 12; open 3 (T-8, T-14, T-15), done 6, 1 running
- G-2 "Understand late-game timeouts": no status yet; open 0, done 1, 0 running
Recent claims on active Goals:
- C-5 (G-1): V-7 loses 60% of late fights after over-extending; 4 of 5 replays, n=5
- C-2 (G-1): Earlier scouting does not change the win rate against the champion (n=40)"""
)

NO_COWORLD_BRIEFING = (
    BRIEFING_HEADING
    + """
Workspace: Coworld none; the frontend must open a session for a Coworld; paused: no; watcher heartbeat just now; credits: unknown
Your Player: id ply_cogs
User identity: available
Directives: none active
No session Coworld: open a Coworld session to access game work."""
)


def _plugin_section(name: str, text: str) -> str:
    """How Hermes wraps a plugin's prompt section."""
    return (
        f"## Plugin Context: {name}\n<!-- hermes-plugin-section-chars:{len(text)} -->\n\n{text}\n"
    )


def session_context_section(
    declared: dict | None,
    capabilities: dict | None,
    *,
    has_coworld: bool,
    role: Role = "orchestrator",
    kind: str = "chat",
) -> str:
    """The ``[session context]`` text as ``plugin/session_context.py`` renders it."""
    parts = ["[session context]", AUTHORITY]
    if declared:
        parts.append("Declared at session open: " + json.dumps(declared, sort_keys=True))
        if declared.get("opening_goal"):
            parts.append(OPENING_GOAL)
    elif kind == "chat":
        parts.append("No session context was declared; the client did not say what it shows.")
    if has_coworld:
        parts.append(
            f"Work only in {COWORLD_ROOT}. "
            f"This session belongs to {COWORLD}; do not change its Coworld."
        )
        parts.append(
            "Native terminal commands inherit this session ID. Named store reads can use: "
            f"research store snapshot --store coworld --coworld {COWORLD}. "
            "Custom research tools already carry this session's binding."
        )
        paths = [f"{COWORLD_CODE}/mixin/skills/cogs-{name}/SKILL.md" for name in BINDINGS]
        parts.append(
            "Game skills are files, not in skill_view. Read the one you need with read_file:\n"
            + "\n".join(paths)
        )
    if capabilities:
        parts.append("Attached clients can show: " + json.dumps(capabilities, sort_keys=True))
    else:
        parts.append(no_client_text(role, kind))
    return "\n\n".join(parts)


def system_prompt(
    role: Role,
    *,
    your_task: str = "",
    declared: dict | None = None,
    capabilities: dict | None = None,
    has_coworld: bool = True,
    kind: str = "chat",
) -> str:
    """The whole system prompt of a session, in the order Hermes assembles it.

    ``your_task`` is the briefing's Task line, ``declared`` and ``capabilities`` the client's
    session context and ``kind`` the session's kind in the store (chat, task or wake)."""
    briefing = BRIEFING.format(your_task=your_task) if has_coworld else NO_COWORLD_BRIEFING
    context = session_context_section(
        declared, capabilities, has_coworld=has_coworld, role=role, kind=kind
    )
    return "\n\n".join(
        [
            build.render(role).strip(),
            HERMES["before_skills"].strip(),
            HERMES["skills_intro"] + skill_index(role) + HERMES["skills_outro"],
            _plugin_section("ide-briefing", briefing),
            _plugin_section("ide-session-context", context),
            HERMES["after_plugins"].replace("{role}", role).strip(),
        ]
    )


# --- the tool list ----------------------------------------------------------------------------


def builtin_names() -> set[str]:
    return {tool["function"]["name"] for tool in HERMES["tools"]}


def session_tools(role: Role, schemas: harness.SchemaSet, model: str) -> list[dict]:
    """``harness.full_toolset`` with the pinned image's built-in tools instead of v0.21.6's."""
    sanitizer, moonshot = harness.hermes_sanitizers()
    ide = [{"type": "function", "function": schema} for schema in schemas.tools(role)]
    tools = sanitizer.sanitize_tool_schemas(
        sorted([*HERMES["tools"], *ide], key=lambda tool: tool["function"]["name"])
    )
    return moonshot.sanitize_moonshot_tools(tools) if moonshot.is_moonshot_model(model) else tools
