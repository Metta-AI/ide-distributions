"""Step 10: start Researcher runs for free slots, drained Sessions and repair Tasks first.

Design B.13 and Appendix H. A slot is ``max_researchers`` minus the task Sessions whose run the
gateway still reports live (statuses are refreshed here, so the count is exact for this tick).
Sessions a pause or a Coworld switch drained resume first with their own session id (H "Sleep
and drain"); then ``tasks.dispatchable`` gives the accepted Tasks, ``high`` priority first, so
a repair Task takes the next slot ahead of other work (B.15).

Each new Task gets a Researcher profile round-robin over ``IDE_RESEARCHER_PROFILES`` (the
pointer lives in ``meta.dispatch_rr``), a minted session id, a Session row and a run whose
first tool call is ``task.claim``: the claim takes the lease, makes the worktree and reads the
run id from the Session row (A3.7), which is why ``sessions.set_run`` happens inside the start.
The ``Idempotency-Key`` is ``task:<id>:<lease epoch>:<attempt>`` (A5.1), and every start goes
through :func:`resume.resume_session`, which writes the run intent (A5.2).

A run that ends without claiming counts as one attempt of its Session (A5.5: no timer kills a
run). When the Session reaches ``recovery.max_attempts`` the Task is left alone and
``recovery.failed`` is emitted once, which raises a management wake. ``RunsBusy`` stops the
dispatch for this tick; the registered Session keeps its row and starts next tick.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Any

from research_core.store import goals, sessions, settings, tasks
from research_core.store.errors import StoreError
from research_core.watcher.hermes_api import mint_session_id
from research_core.watcher.resume import emit_event, is_live, resume_session, run_status

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store
    from research_core.store.events import Event
    from research_core.watcher.context import TickContext

WAKES_DIR = Path(__file__).with_name("wakes")
ROUND_ROBIN_META = "dispatch_rr"
FAILED_META_PREFIX = "recovery.failed:"


def run(ctx: TickContext) -> dict[str, Any]:
    """Resume drained Sessions, then start runs for dispatchable Tasks while slots remain."""
    if ctx.store is None:
        return {"skipped": "no active Coworld"}
    if settings.get(ctx.stores, "paused"):
        return {"skipped": "paused"}
    report = _new_report(ctx)
    _resume_drained(ctx, report)
    if report["deferred"]:
        return report
    for task in tasks.dispatchable(ctx.store):
        if report["slots"] <= 0:
            break
        _dispatch(ctx, task, report)
        if report["deferred"]:
            break
    return report


def handle_resumed(ctx: TickContext, event: Event, store: Store) -> dict[str, Any]:
    """``workspace.resumed`` (a pause ended or a switch came back): drained Sessions first."""
    if ctx.store is None:
        return {"skipped": "no active Coworld"}
    report = _new_report(ctx)
    _resume_drained(ctx, report)
    return report


def render(name: str, **values: Any) -> str:
    """A prompt from ``wakes/<name>.md`` with ``$name`` placeholders filled in."""
    template = Template((WAKES_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return template.safe_substitute({k: "none" if v is None else str(v) for k, v in values.items()})


# --- slots -------------------------------------------------------------------------------------


def _new_report(ctx: TickContext) -> dict[str, Any]:
    live = live_task_sessions(ctx)
    return {
        "live": sorted(live),
        "slots": int(settings.get(ctx.stores, "max_researchers")) - len(live),
        "resumed": [],
        "started": [],
        "skipped": [],
        "failed": [],
        "deferred": None,
    }


def live_task_sessions(ctx: TickContext) -> dict[str, Any]:
    """Task Sessions whose run the gateway reports as not terminal: ``{session_id: status}``."""
    rows = ctx.wstore.q(
        "SELECT id FROM sessions WHERE kind = 'task' AND run_id IS NOT NULL AND ended IS NULL"
        " ORDER BY started, id"
    )
    live: dict[str, Any] = {}
    for row in rows:
        session = sessions.read(ctx.wstore, row["id"])
        status = run_status(ctx, session)
        if is_live(status):
            live[row["id"]] = status
    return live


def _take_slot(report: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("delivered") == "start":
        report["slots"] -= 1
    if result.get("deferred"):
        report["deferred"] = result.get("reason")


# --- drained Sessions --------------------------------------------------------------------------


def drained_sessions(ctx: TickContext) -> list[dict[str, Any]]:
    """Task Sessions of the active Coworld that a drain stopped, oldest first."""
    rows = ctx.wstore.q(
        "SELECT id FROM sessions WHERE drained = 1 AND kind = 'task' AND coworld_id = ?"
        " ORDER BY started, id",
        (ctx.coworld.coworld_id,),
    )
    return [sessions.read(ctx.wstore, row["id"]) for row in rows]


def _resume_drained(ctx: TickContext, report: dict[str, Any]) -> None:
    for session in drained_sessions(ctx):
        task = _held_task(ctx, session)
        if task is None:
            # Nothing to continue (the Task finished or moved on while paused): drop the flag.
            sessions.mark_drained(ctx.wstore, ctx.actor, [session["id"]], False)
            report["skipped"].append({"session": session["id"], "reason": "no Task to continue"})
            continue
        if report["slots"] <= 0:
            return
        n = int(task["id"].removeprefix("T-"))
        text = render(
            "task_resume",
            task_id=task["id"],
            title=task["title"],
            worktree=task["worktree"] or ctx.coworld.worktree_for_task(n),
            branch=task["branch"] or f"task/{n}",
        )
        result = resume_session(ctx, session, text)
        _take_slot(report, result)
        if result.get("deferred"):
            return
        sessions.mark_drained(ctx.wstore, ctx.actor, [session["id"]], False)
        if result.get("run_id"):
            tasks.set_run(ctx.store, ctx.actor, n, result["run_id"])
        report["resumed"].append({"session": session["id"], "task": task["id"], **result})


def _held_task(ctx: TickContext, session: dict[str, Any]) -> dict[str, Any] | None:
    """The in-progress Task this Session holds, or None."""
    if not session.get("task_id"):
        return None
    try:
        task = tasks.read(ctx.store, session["task_id"])
    except StoreError:
        return None
    if task["status"] != "in-progress" or task["held_by"] != session["id"]:
        return None
    return task


# --- new Tasks ---------------------------------------------------------------------------------


def _dispatch(ctx: TickContext, task: dict[str, Any], report: dict[str, Any]) -> None:
    n = int(task["id"].removeprefix("T-"))
    session = latest_task_session(ctx, n)
    if session is not None:
        status = run_status(ctx, session)
        if is_live(status):
            report["skipped"].append({"task": task["id"], "reason": "a run is live"})
            return
        if session["run_id"] is not None:
            # The run ended without claiming: that was one attempt of this Session (A5.5).
            max_attempts = int(settings.get(ctx.stores, "recovery.max_attempts"))
            if session["attempts"] >= max_attempts:
                _give_up(ctx, task, session, report, max_attempts)
                return
    else:
        session = sessions.register(
            ctx.wstore,
            ctx.actor,
            session_id=mint_session_id(),
            profile=_next_profile(ctx),
            kind="task",
            coworld_id=ctx.coworld.coworld_id,
            task_id=n,
            goal_id=task["goal_id"],
        )
    result = resume_session(
        ctx,
        session,
        start_text(ctx, task),
        key_template=f"task:{n}:{task['lease_epoch']}:{{attempt}}",
        attempt_note=_attempt_note(session),
    )
    _take_slot(report, result)
    if result.get("delivered") == "start":
        tasks.set_run(ctx.store, ctx.actor, n, result["run_id"])
        report["started"].append(
            {"task": task["id"], "session": session["id"], "profile": session["profile"], **result}
        )
    elif not result.get("deferred"):
        report["skipped"].append({"task": task["id"], **result})


def latest_task_session(ctx: TickContext, task_id: int) -> dict[str, Any] | None:
    """The newest task Session registered for the Task in the active Coworld, ended or not."""
    found = sessions.for_task(ctx.wstore, task_id, coworld_id=ctx.coworld.coworld_id)
    found = [s for s in found if s["kind"] == "task"]
    return found[-1] if found else None


def _give_up(ctx, task: dict[str, Any], session: dict[str, Any], report, max_attempts: int):
    marker = f"{FAILED_META_PREFIX}{session['id']}"
    reason = (
        f"{session['attempts']} runs of Session {session['id']} ended without claiming"
        f" {task['id']} (recovery.max_attempts = {max_attempts})"
    )
    if ctx.meta(marker) is None:
        emit_event(
            ctx.store,
            ctx.actor,
            "recovery.failed",
            task["id"],
            {"task": task["id"], "session": session["id"], "reason": reason, "stage": "dispatch"},
        )
        ctx.set_meta(marker, session.get("run_id") or "")
        report["failed"].append({"task": task["id"], "session": session["id"], "reason": reason})
    else:
        report["skipped"].append({"task": task["id"], "reason": reason})


def _attempt_note(session: dict[str, Any]) -> str:
    if not session["attempts"]:
        return ""
    return (
        f"(Your previous run ended without claiming the Task; this is attempt"
        f" {session['attempts'] + 1}. Claim it first.)"
    )


def _next_profile(ctx: TickContext) -> str:
    profiles = ctx.researcher_profiles or ("researcher",)
    pointer = int(ctx.meta(ROUND_ROBIN_META, "0") or 0)
    ctx.set_meta(ROUND_ROBIN_META, str(pointer + 1))
    return profiles[pointer % len(profiles)]


def start_text(ctx: TickContext, task: dict[str, Any]) -> str:
    """The ``task_start`` prompt for a Task: the record, its Goal, worktree and first call."""
    n = int(task["id"].removeprefix("T-"))
    experiment = f"X-{task['experiment_id']}" if task["experiment_id"] else None
    end_condition = task["end_condition"]
    if end_condition and task["end_detail"]:
        end_condition = f"{end_condition}: {task['end_detail']}"
    return render(
        "task_start",
        coworld=ctx.coworld.coworld_id,
        task_id=task["id"],
        title=task["title"],
        type=task["type"] + (f" (repair {task['capability']})" if task["capability"] else ""),
        priority=task["priority"],
        goal=_goal_line(ctx.store, task["goal_id"]),
        end_condition=end_condition,
        experiment=experiment,
        depends_on=", ".join(task["depends_on"]) or None,
        description=task["description"],
        reasoning=task["reasoning"] or "",
        worktree=ctx.coworld.worktree_for_task(n),
        branch=f"task/{n}",
    )


def _goal_line(store: Store, goal_id: int | None) -> str:
    if goal_id is None:
        return "workspace maintenance (no Goal)"
    goal = goals.read(store, goal_id)
    return f"{goal['id']} {goal['title']} — end state: {goal['end_state']}"
