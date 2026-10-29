"""Step 6: deliver the Orchestrator's messages by steer or resume; stop abandoned runs; resume a
Researcher whose work a review reopened (design B.13, wake table).

``task.message`` and ``task.answer`` leave ``to_researcher`` rows undelivered. For each
in-progress Task with such rows the holder's Session is resumed through
:func:`resume.resume_session`, which steers a live run or starts one, appends the messages and
marks them delivered. A Session with no live run whose Task waits on something keeps its
messages until the wait resolves: they ride along on that resume instead of starting a run
early (A4.4: the Researcher ended its run on purpose).

``handle_abandoned`` marks the holder's Session ``watcher:abandon`` before stopping its run,
so recovery does not count the stop as a death (A5.6), then ends the Session; the branch is
kept. ``handle_reviewed`` resumes the Researcher with the reason when the review reopened the
Task; an accepted review is the merge step's business (``merge == pending``). Because
``tasks.review`` also queues a ``Reopened: <reason>`` message that step 6 may already have
delivered in the same tick, the handler does nothing when that message is marked delivered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from research_core.store import sessions, tasks
from research_core.watcher.hermes_api import RunsError
from research_core.watcher.resume import holder_session, is_live, resume_session, run_status

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store
    from research_core.store.events import Event
    from research_core.watcher.context import TickContext

HEADER = "The Orchestrator sent you a message; read it below and act on it."


def run(ctx: TickContext) -> dict[str, Any]:
    """Deliver pending ``to_researcher`` messages of the active Coworld's in-progress Tasks."""
    if ctx.store is None:
        return {"skipped": "no active Coworld"}
    store = ctx.store
    report: dict[str, Any] = {"steered": [], "resumed": [], "held": [], "deferred": []}
    for task in tasks.list(store, status="in-progress"):
        pending = tasks.pending_messages(store, task["id"], "to_researcher")
        if not pending:
            continue
        session = holder_session(ctx, task)
        if session is None:
            report["held"].append({"task": task["id"], "reason": "no holder Session"})
            continue
        if task["waiting_on"] and not is_live(run_status(ctx, session)):
            kind = task["waiting_on"]["kind"]
            report["held"].append(
                {"task": task["id"], "reason": f"delivered when the {kind} wait resolves"}
            )
            continue
        result = resume_session(ctx, session, HEADER)
        entry = {"task": task["id"], "messages": len(pending), **result}
        if result.get("delivered") == "steer":
            report["steered"].append(entry)
        elif result.get("delivered") == "start":
            tasks.set_run(store, ctx.actor, task["id"], result["run_id"])
            report["resumed"].append(entry)
        else:
            report["deferred"].append(entry)
    return report


def handle_abandoned(ctx: TickContext, event: Event, store: Store) -> dict[str, Any]:
    """``task.abandoned``: stop the previous holder's run (marked as the watcher's stop) and
    end its Session. The branch stays."""
    session_id = event.payload.get("previous_holder")
    if not session_id:
        return {"skipped": f"{event.subject} was not held"}
    session = sessions.read(ctx.wstore, session_id)
    if session is None:
        return {"skipped": f"no Session row for {session_id}"}
    result: dict[str, Any] = {"task": event.subject, "session": session_id}
    if is_live(run_status(ctx, session)):
        sessions.set_stopped_by(ctx.wstore, ctx.actor, session_id, "watcher:abandon")
        try:
            status = ctx.hermes.stop(session["run_id"], profile=session["profile"])
            result.update(stopped=session["run_id"], status=status.status)
        except RunsError as error:
            result["stop_error"] = error.detail
    sessions.end(ctx.wstore, ctx.actor, session_id)
    result["ended"] = True
    return result


def handle_reviewed(ctx: TickContext, event: Event, store: Store) -> dict[str, Any]:
    """``task.reviewed``: a rejected review resumes the Researcher with the reason."""
    if event.payload.get("accept"):
        return {"skipped": "accepted; the merge step reads merge == pending"}
    task = tasks.read(store, event.subject)
    if task["status"] != "in-progress":
        return {"skipped": f"{task['id']} is {task['status']}, not reopened"}
    reason = event.payload.get("reason") or "see the review"
    if any(
        m["direction"] == "to_researcher" and m["text"] == f"Reopened: {reason}" and m["delivered"]
        for m in task["messages"]
    ):
        return {"skipped": "the reopen message was already delivered"}
    session = holder_session(ctx, task)
    if session is None:
        return {"skipped": f"{task['id']} has no holder Session to resume"}
    result = resume_session(ctx, session, f"Review reopened {task['id']}: {reason}. Continue.")
    if result.get("delivered") == "start":
        tasks.set_run(store, ctx.actor, task["id"], result["run_id"])
    return {"task": task["id"], **result}
