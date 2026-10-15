"""Hermes plugin ``researcher_guard``: two rules a researcher worker cannot break. STDLIB ONLY.

1. Only the orchestrator creates work. Hermes gives every dispatcher-spawned worker the native
   ``kanban`` toolset, including ``kanban_create``, ``kanban_link`` and ``kanban_schedule``,
   and the YAML deny list matches shell commands, not these tools. A card a researcher made
   would bypass the orchestrator's plan.
2. Only ``workspace integrate`` pushes canonical ``main``. A worker committing canonical by
   hand broke the workspace once (2026-10-09); the deny list blocks force pushes but not an
   ordinary ``git push origin main``. Integrate pushes from its own process, which this hook
   never sees.

Everything else the old 1,641-line policy did is gone on purpose (design J-H). A script that
pushes ``main`` from Python is not caught here; the next integrate's overlap check notices
the unexpected commit (risk K11).
"""

import re
import shlex

BLOCKED_TOOLS = {
    "kanban_create": "only the orchestrator creates cards",
    "kanban_link": "only the orchestrator links cards",
    "kanban_schedule": "only the orchestrator schedules cards",
}
PROTECTED_REFS = {"main", "refs/heads/main"}
_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def pushes_main(command: str) -> bool:
    """Whether a shell command line runs a ``git push`` that writes ``main``."""
    for segment in _SEGMENT.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        while words and _ASSIGNMENT.match(words[0]):  # GIT_TRACE=1 git push ...
            words = words[1:]
        if not words or words[0].rsplit("/", 1)[-1] != "git":  # the command, not a mention
            continue
        rest = words[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in _GIT_OPTIONS_WITH_VALUE else rest[1:]
        if not rest or rest[0] != "push":
            continue
        options = [word for word in rest[1:] if word.startswith("-")]
        operands = [word for word in rest[1:] if not word.startswith("-")]
        if {"--all", "--mirror"} & {option.split("=")[0] for option in options}:
            return True
        for refspec in operands[1:]:
            if refspec.lstrip("+").split(":")[-1] in PROTECTED_REFS:
                return True
    return False


def pre_tool_call(tool_name=None, args=None, **kwargs):
    if tool_name in BLOCKED_TOOLS:
        reason = BLOCKED_TOOLS[tool_name]
        return {
            "action": "block",
            "message": f"{tool_name} is blocked: {reason}. Propose follow-up work in your "
            "handoff's `next` or as a `suggestion:` comment on your own card.",
        }
    command = (args or {}).get("command") if tool_name == "terminal" else None
    if isinstance(command, str) and pushes_main(command):
        return {
            "action": "block",
            "message": "pushing main is blocked: run `\"$RESEARCH_CLI\" workspace integrate`, "
            "which validates, merges and pushes for you.",
        }
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
