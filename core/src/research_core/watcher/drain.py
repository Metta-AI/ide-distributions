"""Drain and the "has work" signal (design Appendix H "Sleep and drain"; A5.6, A5.7, A5.11).

``run`` is what an explicit pause (the tick's step 1, once per pause) and ``research workspace
drain`` call: every task Session whose run the gateway still reports live is marked
``stopped_by = watcher:drain`` *before* ``hermes.stop`` (so recovery never counts the stop as
a death, A5.6), its run is stopped and the Session is flagged ``drained``; background
processes a Researcher waits on (``waiting_on.kind == process``) get SIGTERM; then the step
waits up to a minute for the stopped runs to end and emits ``drain.done``. After the wake the
dispatch step resumes drained Sessions first, with their own session ids.

``has_work`` is the signal hosting reads to keep the workspace awake. It is true for a live
Researcher run, a Task waiting on XP, credits, a submission or a background process, an open
autonomous-submission window **with a candidate**, or a queued wake. The window alone is not
work (A5.11): ``submissions.until`` is null by default, so an "open window" would hold every
workspace awake forever; a candidate is a succeeded Task that answered ``submission_candidate:
yes`` with a tested Version no ``version.submitted`` event names yet.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from research_core.store import sessions, settings, tasks
from research_core.store._common import parse_iso
from research_core.store.db import Store
from research_core.watcher import dispatch, wakes
from research_core.watcher.hermes_api import RunsError
from research_core.watcher.resume import emit_event

if TYPE_CHECKING:  # pragma: no cover
    from research_core.watcher.context import TickContext

STOP_REASON = "watcher:drain"
WAIT_S = 60.0
POLL_S = 1.0
BUSY_WAIT_KINDS = frozenset({"xp", "credits", "submission", "process"})


def run(
    ctx: TickContext, *, wait_s: float = WAIT_S, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """Stop live Researcher runs and their background processes; return when quiet."""
    stopped: list[str] = []
    errors: list[dict[str, str]] = []
    for session_id in dispatch.live_task_sessions(ctx):
        session = sessions.read(ctx.wstore, session_id)
        sessions.set_stopped_by(ctx.wstore, ctx.actor, session_id, STOP_REASON)
        try:
            ctx.hermes.stop(session["run_id"], profile=session["profile"])
        except RunsError as error:
            sessions.set_stopped_by(ctx.wstore, ctx.actor, session_id, None)
            errors.append({"session": session_id, "error": error.detail})
            continue
        stopped.append(session_id)
    if stopped:
        sessions.mark_drained(ctx.wstore, ctx.actor, stopped, True)
    processes = stop_processes(ctx)
    quiet = wait_quiet(ctx, stopped, wait_s=wait_s, sleep=sleep)
    emit_event(
        ctx.wstore,
        ctx.actor,
        "drain.done",
        "workspace",
        {"sessions": stopped, "processes": processes, "quiet": quiet, "errors": errors},
    )
    return {"stopped": stopped, "processes": processes, "quiet": quiet, "errors": errors}


def stop_processes(ctx: TickContext) -> list[int]:
    """SIGTERM every background process an in-progress Task waits on; the pids signalled."""
    if ctx.store is None:
        return []
    signalled: list[int] = []
    for task in tasks.list(ctx.store, status="in-progress"):
        waiting = task["waiting_on"] or {}
        if waiting.get("kind") != "process":
            continue
        pid = _pid(waiting.get("ref"))
        if pid is None:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
        signalled.append(pid)
    return signalled


def wait_quiet(
    ctx: TickContext, session_ids: list[str], *, wait_s: float, sleep: Callable[[float], None]
) -> bool:
    """Poll the stopped runs until every one is terminal or ``wait_s`` has passed."""
    deadline = time.monotonic() + wait_s
    while True:
        live = [
            session_id
            for session_id in session_ids
            if _still_running(ctx, sessions.read(ctx.wstore, session_id))
        ]
        if not live:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(POLL_S)


# --- has work ---------------------------------------------------------------------------------


def has_work(ctx: TickContext) -> bool:
    """Whether hosting should keep the workspace awake (see the module docstring)."""
    if dispatch.live_task_sessions(ctx):
        return True
    if ctx.store is not None:
        if any(
            (task["waiting_on"] or {}).get("kind") in BUSY_WAIT_KINDS
            for task in tasks.list(ctx.store, status="in-progress")
        ):
            return True
        if autonomous_candidate(ctx):
            return True
    return wakes.has_queued_wakes(ctx)


def autonomous_candidate(ctx: TickContext) -> bool:
    """An open autonomous-submission window with a Version waiting to be submitted (A5.11)."""
    store = ctx.store
    if settings.get(store, "submissions.mode") != "autonomous":
        return False
    until = settings.get(store, "submissions.until")
    if until is not None and parse_iso(until) <= ctx.now:
        return False
    submitted = _submitted_versions(store)
    for task in tasks.list(store, status="complete"):
        checklist = task["checklist"] or {}
        if task["outcome"] != "succeeded" or checklist.get("submission_candidate") != "yes":
            continue
        tested = checklist.get("versions") or []
        if any(version not in submitted for version in tested):
            return True
    return False


# --- helpers ----------------------------------------------------------------------------------


def _still_running(ctx: TickContext, session: dict[str, Any] | None) -> bool:
    if session is None or not session.get("run_id"):
        return False
    try:
        status = ctx.hermes.status(session["run_id"], profile=session["profile"])
    except RunsError:
        return False  # an unreachable gateway cannot be waited on; it is not our run any more
    return not status.terminal


def _submitted_versions(store: Store) -> set[str]:
    rows = store.q("SELECT DISTINCT subject FROM events WHERE kind = 'version.submitted'")
    return {row["subject"] for row in rows}


def _pid(ref: Any) -> int | None:
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref if ref > 1 else None
    if isinstance(ref, str) and ref.strip().isdigit():
        return int(ref.strip()) if int(ref.strip()) > 1 else None
    return None
