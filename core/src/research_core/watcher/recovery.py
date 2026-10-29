"""Step 5: end finished Sessions, recover dead runs, replace a Session that keeps dying, and
flag stuck Tasks (design B.4, B.13; amendments A5.3, A5.5, A5.6).

Every task and wake Session with a run is polled. A terminal run ends the Session row (wake
events are acknowledged by the wake step, not here). A task Session whose run ended
``failed``, ``interrupted``, ``cancelled`` or ``unknown`` (the gateway forgot it) while its
Task is in progress with nothing to wait on is a dead run: the step emits ``session.dead`` and
the handler decides. A stop the watcher asked for itself (``sessions.stopped_by``: drain,
replace, abandon) is not a death and is skipped (A5.6).

``handle_dead`` resumes the same Session (``resume_session``, so a live run is steered and a
dead one restarted with its history) while its attempts are below ``recovery.max_attempts``.
Then it replaces it: the old Session is marked ``watcher:replace`` and its run stopped, the
Task is released (unclaimed, lease cleared; the new Session's ``task.claim`` raises the
epoch, so the old run's late writes are refused and the merge keeps using
``completed_commit``), and a fresh Session starts with a summary of the Task, its Experiment,
the branch's commits and the last messages of the old transcript. If even that start fails,
``recovery.failed`` raises a management wake.

A stuck Task is in progress, waits on nothing, has no live run and its lease was last renewed
more than ``tasks.LEASE_FRESH_SECONDS`` ago (B.4); the step emits ``task.stuck`` once per stale
lease (``meta.stuck:<task>`` remembers the lease time it reported) and ``handle_stuck`` takes
the same path as a death. Leases are renewed by the holder's own tool calls, never here.
"""

from __future__ import annotations

from string import Template
from typing import TYPE_CHECKING, Any

from research_core import gitops
from research_core.store import experiments, sessions, settings, tasks
from research_core.store.actors import Actor
from research_core.store.errors import StoreError
from research_core.watcher.hermes_api import RunsError, mint_session_id
from research_core.watcher.resume import (
    emit_event,
    holder_session,
    is_live,
    resume_session,
    run_status,
)

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store
    from research_core.store.events import Event
    from research_core.watcher.context import TickContext

DEAD_STATUSES = frozenset({"failed", "interrupted", "cancelled", "unknown"})
STUCK_META_PREFIX = "stuck:"
DEAD_TEXT = "Your previous run ended unexpectedly; continue from your last commit."
TRANSCRIPT_LIMIT = 20
MESSAGE_CHARS = 600
COMMIT_LIMIT = 20

REPLACEMENT = Template(
    """You are taking over Task $task_id after its previous Session ($old_session) $why.
The watcher replaced that Session after $attempts attempts. Its branch and records are yours
now; continue from the last commit and do not redo finished work.

## Task $task_id — $title

- Type: $type (priority $priority)
- Description: $description
- Experiment: $experiment

## Branch $branch (newest commit first)

$commits

## Last messages of the previous Session (oldest first)

$transcript

Your first tool call is `task.claim {task: $task_id}`: it takes the lease at a new epoch and
creates your worktree at `$worktree` from the branch tip. Then read the Task with `task.read`
and continue."""
)


def run(ctx: TickContext) -> dict[str, Any]:
    """Poll every Session with a run; end the finished ones; emit ``session.dead`` and
    ``task.stuck`` for the handlers."""
    report: dict[str, Any] = {"ended": [], "dead": [], "stuck": [], "skipped": [], "errors": []}
    for session in sessions_with_runs(ctx):
        try:
            status = run_status(ctx, session)
        except RunsError as error:
            report["errors"].append({"session": session["id"], "error": error.detail})
            continue
        if status is None or not status.terminal:
            continue
        sessions.end(ctx.wstore, ctx.actor, session["id"])
        report["ended"].append(
            {"session": session["id"], "kind": session["kind"], "status": status.status}
        )
        if session["kind"] != "task" or status.status not in DEAD_STATUSES:
            continue
        if session["stopped_by"]:
            report["skipped"].append(
                {"session": session["id"], "reason": f"stopped by {session['stopped_by']}"}
            )
            continue
        task = _task_of(ctx, session)
        if task is None or not _recoverable(task, session["id"]):
            continue
        emit_event(
            ctx.store,
            ctx.actor,
            "session.dead",
            task["id"],
            {
                "session": session["id"],
                "task": task["id"],
                "run": session["run_id"],
                "status": status.status,
                "error": status.error,
            },
        )
        report["dead"].append({"session": session["id"], "task": task["id"]})
    _flag_stuck(ctx, report)
    return report


def sessions_with_runs(ctx: TickContext) -> list[dict[str, Any]]:
    """Task and wake Sessions with a run that has not been ended yet, oldest first."""
    rows = ctx.wstore.q(
        "SELECT id FROM sessions WHERE run_id IS NOT NULL AND ended IS NULL"
        " AND kind IN ('task', 'wake') ORDER BY started, id"
    )
    return [sessions.read(ctx.wstore, row["id"]) for row in rows]


# --- handlers ----------------------------------------------------------------------------------


def handle_dead(ctx: TickContext, event: Event, store: Store) -> dict[str, Any]:
    """``session.dead``: resume the Session, or replace it after ``recovery.max_attempts``."""
    payload = event.payload
    task = tasks.read(store, payload.get("task") or event.subject)
    session = sessions.read(ctx.wstore, payload["session"]) if payload.get("session") else None
    why = f"ended {payload.get('status') or 'unexpectedly'}"
    return recover(ctx, store, task, session, why)


def handle_stuck(ctx: TickContext, event: Event, store: Store) -> dict[str, Any]:
    """``task.stuck``: the same path as a death (B.4)."""
    task = tasks.read(store, event.payload.get("task") or event.subject)
    return recover(ctx, store, task, holder_session(ctx, task), "went quiet without a run")


def recover(
    ctx: TickContext, store: Store, task: dict[str, Any], session: dict | None, why: str
) -> dict[str, Any]:
    if not _recoverable(task, session["id"] if session else task["held_by"]):
        return {"skipped": f"{task['id']} is not held in progress without a wait any more"}
    if session is None and task["held_by"]:
        session = sessions.read(ctx.wstore, task["held_by"])
    if is_live(run_status(ctx, session)):
        return {"skipped": f"{task['id']} has a live run again"}
    max_attempts = int(settings.get(ctx.stores, "recovery.max_attempts"))
    if session is not None and session["attempts"] < max_attempts:
        note = f"(Attempt {session['attempts'] + 1} of {max_attempts} for this Session.)"
        result = resume_session(ctx, session, DEAD_TEXT, attempt_note=note)
        if result.get("delivered") == "start":
            tasks.set_run(store, ctx.actor, task["id"], result["run_id"])
        return {"action": "resumed", "task": task["id"], **result}
    return replace(ctx, store, task, session, why)


def replace(
    ctx: TickContext, store: Store, task: dict[str, Any], old: dict | None, why: str
) -> dict[str, Any]:
    """Stop the old Session, release the Task and start a fresh Session with a summary."""
    n = int(task["id"].removeprefix("T-"))
    holder = task["held_by"]
    attempts = old["attempts"] if old else 0
    result: dict[str, Any] = {"task": task["id"], "old_session": old["id"] if old else holder}
    if old is not None:
        sessions.set_stopped_by(ctx.wstore, ctx.actor, old["id"], "watcher:replace")
        if old["run_id"]:
            try:
                result["stopped"] = ctx.hermes.stop(old["run_id"], profile=old["profile"]).status
            except RunsError as error:
                result["stop_error"] = error.detail
    summary = replacement_text(ctx, store, task, old, why, attempts)
    try:
        tasks.release(
            store, Actor.session(holder), n, reason=f"replaced after {attempts} failed runs: {why}"
        )
    except StoreError as error:
        return _replace_failed(ctx, store, task, result, f"release refused: {error}")
    new = sessions.register(
        ctx.wstore,
        ctx.actor,
        session_id=mint_session_id(),
        profile=old["profile"] if old else (ctx.researcher_profiles or ("researcher",))[0],
        kind="task",
        coworld_id=ctx.coworld.coworld_id,
        task_id=n,
        goal_id=task["goal_id"],
        parent=old["id"] if old else None,
    )
    result["new_session"] = new["id"]
    started = resume_session(ctx, new, summary)
    if started.get("delivered") != "start":
        return _replace_failed(ctx, store, task, result, started.get("reason") or "no run started")
    tasks.set_run(store, ctx.actor, n, started["run_id"])
    return {"action": "replaced", **result, **started}


def replacement_text(
    ctx: TickContext, store: Store, task: dict, old: dict | None, why: str, attempts: int
) -> str:
    n = int(task["id"].removeprefix("T-"))
    branch = task["branch"] or f"task/{n}"
    return REPLACEMENT.substitute(
        task_id=task["id"],
        old_session=old["id"] if old else task["held_by"],
        why=why,
        attempts=attempts,
        title=task["title"],
        type=task["type"],
        priority=task["priority"],
        description=task["description"],
        experiment=_experiment_line(store, task["experiment_id"]),
        branch=branch,
        commits=_commit_lines(ctx, branch),
        transcript=_transcript_lines(ctx, old),
        worktree=ctx.coworld.worktree_for_task(n),
    )


# --- stuck Tasks -------------------------------------------------------------------------------


def _flag_stuck(ctx: TickContext, report: dict[str, Any]) -> None:
    if ctx.store is None:
        return
    for task in tasks.list(ctx.store, status="in-progress"):
        if task["waiting_on"] or not task["stuck"]:
            continue
        session = holder_session(ctx, task)
        if session is not None and (session["drained"] or is_live(run_status(ctx, session))):
            continue
        marker = f"{STUCK_META_PREFIX}{task['id']}"
        if ctx.meta(marker) == str(task["lease_renewed"]):
            continue
        emit_event(
            ctx.store,
            ctx.actor,
            "task.stuck",
            task["id"],
            {
                "task": task["id"],
                "session": task["held_by"],
                "run": session["run_id"] if session else None,
                "lease_renewed": task["lease_renewed"],
            },
        )
        ctx.set_meta(marker, str(task["lease_renewed"]))
        report["stuck"].append({"task": task["id"], "session": task["held_by"]})


# --- helpers -----------------------------------------------------------------------------------


def _recoverable(task: dict[str, Any], session_id: str | None) -> bool:
    return (
        task["status"] == "in-progress"
        and not task["waiting_on"]
        and task["held_by"] is not None
        and task["held_by"] == session_id
    )


def _task_of(ctx: TickContext, session: dict[str, Any]) -> dict[str, Any] | None:
    """The Session's Task in the active Coworld's store; None for another Coworld's."""
    if ctx.store is None or ctx.coworld is None or not session.get("task_id"):
        return None
    if session.get("coworld_id") not in (None, ctx.coworld.coworld_id):
        return None
    try:
        return tasks.read(ctx.store, session["task_id"])
    except StoreError:
        return None


def _replace_failed(ctx, store: Store, task: dict, result: dict, reason: str) -> dict[str, Any]:
    emit_event(
        store,
        ctx.actor,
        "recovery.failed",
        task["id"],
        {
            "task": task["id"],
            "session": result.get("new_session"),
            "old_session": result.get("old_session"),
            "reason": reason,
            "stage": "replace",
        },
    )
    return {"action": "replace_failed", "reason": reason, **result}


def _experiment_line(store: Store, experiment_id: int | None) -> str:
    if not experiment_id:
        return "none"
    try:
        experiment = experiments.read(store, experiment_id)
    except StoreError:
        return f"X-{experiment_id}"
    return f"{experiment['id']} ({experiment['status']}): {experiment['summary']}"


def _commit_lines(ctx: TickContext, branch: str) -> str:
    try:
        entries = gitops.log(ctx.coworld.code, branch, COMMIT_LIMIT)
    except (gitops.GitError, OSError):
        return "(the branch has no readable commits yet)"
    if not entries:
        return "(no commits)"
    return "\n".join(f"- {entry['sha'][:10]} {entry['subject']}" for entry in entries)


def _transcript_lines(ctx: TickContext, old: dict | None) -> str:
    if old is None:
        return "(no previous Session transcript)"
    try:
        messages = ctx.hermes.session_messages(old["id"], profile=old["profile"])
    except RunsError as error:
        return f"(transcript unavailable: {error.detail})"
    lines = []
    for message in messages[-TRANSCRIPT_LIMIT:]:
        role = message.get("role") or "?"
        content = message.get("content")
        if not isinstance(content, str):
            content = str(content)
        content = " ".join(content.split())
        if len(content) > MESSAGE_CHARS:
            content = content[: MESSAGE_CHARS - 1] + "…"
        lines.append(f"- {role}: {content}")
    return "\n".join(lines) or "(the transcript is empty)"
