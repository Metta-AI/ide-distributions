"""Task ownership and workspace boundaries; native completion stays native."""

import os
import re
import shlex
from contextlib import closing

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


def pre_tool_call(tool_name=None, args=None, **kwargs):
    task_id = os.environ.get("HERMES_KANBAN_TASK")
    if task_id:
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_db_connect import connect

        run_id = int(os.environ["HERMES_KANBAN_RUN_ID"])
        with closing(connect()) as conn, kb.write_txn(conn):
            task = kb.get_task(conn, task_id)
            if task.status != "running" or task.current_run_id != run_id:
                return {"action": "block", "message": "This native run no longer owns the task. Stop."}
            kb._append_event(conn, task_id, "operation_admitted", {"tool": tool_name}, run_id=run_id)
    if tool_name in BLOCKED_TOOLS:
        reason = BLOCKED_TOOLS[tool_name]
        return {
            "action": "block",
            "message": f"{tool_name} is blocked: {reason}. Propose follow-up work in your "
            "handoff's `next` or as a `suggestion:` comment on your own card.",
        }
    command = (args or {}).get("command") if tool_name == "terminal" else None
    if isinstance(command, str) and runs_git_push(command):
        return {
            "action": "block",
            "message": 'git push is blocked: run `"$RESEARCH_CLI" workspace stage`, '
            "which preserves your task branch; the coordinator owns integration.",
        }
    return None


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
