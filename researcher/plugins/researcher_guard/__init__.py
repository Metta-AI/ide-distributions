"""Hermes plugin ``researcher_guard``: researcher completion and workspace rules. STDLIB ONLY.

1. Only the orchestrator creates work. Hermes gives every dispatcher-spawned worker the native
   ``kanban`` toolset, including ``kanban_create``, ``kanban_link`` and ``kanban_schedule``,
   and the YAML deny list matches shell commands, not these tools. A card a researcher made
   would bypass the orchestrator's plan.
2. Only ``workspace integrate`` pushes. A worker committing canonical by hand broke the
   workspace once (2026-10-09). Integrate pushes the card branch and ``main`` from its own
   process, which this hook never sees, so a worker never needs ``git push``; blocking every
   terminal push also covers the implicit ones (``git push`` from the clone, ``origin HEAD``).

Everything else the old 1,641-line policy did is gone on purpose (design J-H). A script that
pushes ``main`` from Python is not caught here; the next integrate's overlap check notices
the unexpected commit (risk K11).
"""

import copy
import json
import os
import re
import shlex
import subprocess

BLOCKED_TOOLS = {
    "kanban_create": "only the orchestrator creates cards",
    "kanban_link": "only the orchestrator links cards",
    "kanban_schedule": "only the orchestrator schedules cards",
}
_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def runs_git_push(command: str) -> bool:
    """Whether a shell command line runs ``git push`` (as a command, not a mention)."""
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
        if rest and rest[0] == "push":
            return True
    return False


def completion_problem(args, read_record):
    metadata = args.get("metadata")
    if not isinstance(metadata, dict):
        return (
            "Include the card skill's metadata handoff; a summary alone is not completion evidence."
        )
    outcome = metadata.get("outcome")
    if outcome not in {"done", "partial", "dropped", "failed", "blocked"}:
        return "metadata.outcome must be one of: done, partial, dropped, failed, blocked."
    journal = metadata.get("journal")
    if not isinstance(journal, str) or not journal.startswith("ar://journal/J-"):
        return "metadata.journal must reference the integrated journal."
    record = read_record(journal.removeprefix("ar://journal/"))
    if not record["ok"]:
        return "The journal could not be read from canonical. Integrate it before completing."
    meta = record["data"]["meta"]
    if meta.get("card") != args.get("task_id") or meta.get("outcome") != outcome:
        return "The canonical journal must belong to this task and have the same outcome as the handoff."
    if (
        outcome == "done"
        and metadata.get("version_id")
        and not metadata.get("platform_policy_version_id")
    ):
        return "A completed upload needs its platform policy version receipt; otherwise report partial or blocked."
    return None


def pre_tool_call(tool_name=None, args=None, **kwargs):
    if tool_name in BLOCKED_TOOLS:
        reason = BLOCKED_TOOLS[tool_name]
        return {
            "action": "block",
            "message": f"{tool_name} is blocked: {reason}. Propose follow-up work in your "
            "handoff's `next` or as a `suggestion:` comment on your own card.",
        }
    if tool_name == "kanban_complete":

        def read_record(record_id):
            from agent.secret_scope import build_profile_secret_scope, current_secret_scope
            from hermes_constants import get_hermes_home

            home = get_hermes_home()
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("RESEARCH_", "OPENROUTER_"))
                and key
                not in {
                    "SOFTMAX_PLAYER_CREDENTIAL",
                    "COGAMES_API_URL",
                    "VIRTUAL_ENV",
                    "UV_PROJECT_ENVIRONMENT",
                    "PYTHONPATH",
                    "PYTHONHOME",
                }
            }
            scope = current_secret_scope()
            env.update(build_profile_secret_scope(home) if scope is None else scope)
            env["HERMES_HOME"] = str(home)
            root = env.get("RESEARCH_WORKSPACE", str(home / "workspace"))
            result = subprocess.run(
                [str(home / "bin/research"), "records", "show", record_id, "--root", root],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            return json.loads(result.stdout)

        problem = completion_problem(args or {}, read_record)
        if problem:
            return {"action": "block", "message": problem}
    command = (args or {}).get("command") if tool_name == "terminal" else None
    if isinstance(command, str) and runs_git_push(command):
        return {
            "action": "block",
            "message": 'git push is blocked: run `"$RESEARCH_CLI" workspace integrate`, '
            "which commits, pushes the card branch, validates, merges and pushes main for you.",
        }
    return None


def register(ctx) -> None:
    # Keep native ownership, completion, and artifact handling. Only specialize the
    # model-facing contract through Hermes' supported, profile-scoped override.
    import tools.kanban_tools  # noqa: F401
    from tools.registry import registry

    native = registry.get_entry("kanban_complete")
    schema = copy.deepcopy(native.schema)
    schema["description"] = (
        "Complete this research task after integration. Supply a findings-first summary and "
        "metadata with the canonical journal and matching outcome. Uploaded versions require "
        "version_id and platform_policy_version_id receipts. Use awaiting for pending experience "
        "requests, credits, or permits. Native worker ownership and completion checks still apply."
    )
    schema["parameters"]["properties"]["metadata"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "journal"],
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["done", "partial", "dropped", "failed", "blocked"],
            },
            "journal": {
                "type": "string",
                "description": "Integrated canonical ar://journal/J-... receipt.",
            },
            "records_changed": {"type": "array", "items": {"type": "string"}},
            "merged": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "properties": {"card_sha": {"type": "string"}, "main_sha": {"type": "string"}},
                "required": ["card_sha", "main_sha"],
            },
            "experiment": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string"},
                    "decision": {"type": ["string", "null"]},
                },
                "required": ["id", "status", "decision"],
            },
            "awaiting": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["xp"],
                        "properties": {"xp": {"type": "array", "items": {"type": "string"}}},
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["credits"],
                        "properties": {"credits": {"type": "boolean", "const": True}},
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["permit"],
                        "properties": {"permit": {"type": "string"}},
                    },
                ],
            },
            "next": {"type": ["string", "null"]},
            "version_id": {"type": ["string", "null"]},
            "platform_policy_version_id": {"type": ["string", "null"]},
        },
    }
    schema["parameters"]["required"] = ["task_id", "summary", "metadata"]
    ctx.register_tool(
        name="kanban_complete",
        toolset=native.toolset,
        schema=schema,
        handler=native.handler,
        check_fn=native.check_fn,
        override=True,
        requires_env=native.requires_env,
        is_async=native.is_async,
        emoji=native.emoji,
    )
    ctx.register_hook("pre_tool_call", pre_tool_call)
