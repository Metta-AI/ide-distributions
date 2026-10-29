"""The one way the watcher gets text to a Session: steer its live run, else start one (A5.3).

Every path that continues a Session (a resolved wait, a dead run, a drained Session after a
pause, an Orchestrator message, a reopened review, a fresh dispatch) calls
:func:`resume_session`. It reads the run's status first: ``running`` or ``queued`` means steer
(``queued`` and ``stopping`` runs refuse steers, so the caller tries again next tick); anything
else means a new run on the same session id, which Hermes resumes with its history.

Starts are crash-tolerant (A5.1, A5.2). Before every start a ``run`` intent is written with the
key ``<session>:<attempt>``; the start carries an ``Idempotency-Key`` that names the same
attempt; after ``sessions.set_run`` commits the intent is finished with the run id. A crash
between the start and the commit leaves the intent pending: the next start reuses the same
attempt and key, so the gateway replays the stored run instead of starting a second one. A
pending intent of an attempt that did commit is closed from the Session row before any new
start. ``RunsConflict`` (the key was used with another body) bumps the attempt once;
``RunsBusy`` defers to the next tick with the intent marked failed, so the retry is visible.

Pending ``to_researcher`` messages ride along on every resume of a task Session and are marked
delivered once the text reached the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from research_core.store import intents, sessions, tasks
from research_core.store._common import parse_display_id
from research_core.store.actors import Actor
from research_core.store.errors import StoreError
from research_core.watcher.hermes_api import RunsBusy, RunsConflict, RunStatus

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store
    from research_core.store.events import Event
    from research_core.watcher.context import TickContext

STEERABLE = frozenset({"running", "queued"})
DEFAULT_KEY_TEMPLATE = "session:{session_id}:{attempt}"
# How many idempotency keys one resume may burn through on 409 conflicts before deferring.
MAX_KEY_BUMPS = 3
MESSAGE_LIMIT = 2000  # characters of one Orchestrator message carried on a resume


def resume_session(
    ctx: TickContext,
    session: dict[str, Any],
    text: str,
    *,
    attempt_note: str = "",
    key_template: str = DEFAULT_KEY_TEMPLATE,
    with_pending_messages: bool = True,
) -> dict[str, Any]:
    """Get ``text`` to the Session: ``{"delivered": "steer"|"start", "run_id"}`` or
    ``{"deferred": True, "reason"}`` when the caller must try again next tick.

    ``attempt_note`` is appended to the text (for example which attempt this is).
    ``key_template`` formats the ``Idempotency-Key`` with ``session_id`` and ``attempt``;
    dispatch uses ``task:<id>:<epoch>:{attempt}`` (A5.1). ``with_pending_messages`` appends the
    undelivered Orchestrator messages of the Session's Task and marks them delivered.
    """
    session_id = session["id"]
    message_ids: list[int] = []
    if with_pending_messages:
        block, message_ids = _pending_message_block(ctx, session)
        text = f"{text}\n\n{block}" if block else text
    if attempt_note:
        text = f"{text}\n\n{attempt_note}"
    status = run_status(ctx, session)
    if status is not None and status.status in STEERABLE:
        if ctx.hermes.steer(session["run_id"], text, profile=session["profile"]):
            result = {"session": session_id, "delivered": "steer", "run_id": session["run_id"]}
        else:
            result = {
                "session": session_id,
                "deferred": True,
                "reason": f"run {session['run_id']} is {status.status} and refused the steer",
            }
    elif status is not None and not status.terminal:
        result = {
            "session": session_id,
            "deferred": True,
            "reason": f"run {session['run_id']} is {status.status}; neither steerable nor over",
        }
    else:
        result = _start(ctx, session, text, key_template)
    if result.get("delivered") and message_ids:
        tasks.mark_delivered(ctx.store, ctx.actor, message_ids)
        result["messages_delivered"] = len(message_ids)
    return result


def run_status(ctx: TickContext, session: dict[str, Any] | None) -> RunStatus | None:
    """The Session's run as the gateway reports it now; None without a run."""
    if not session or not session.get("run_id"):
        return None
    return ctx.hermes.status(session["run_id"], profile=session["profile"])


def is_live(status: RunStatus | None) -> bool:
    """A run that still occupies the gateway (not terminal), whatever its phase."""
    return status is not None and not status.terminal


def holder_session(ctx: TickContext, task: dict[str, Any]) -> dict[str, Any] | None:
    """The Session row of the Task's holder, or None when the Task is not held or unknown."""
    if not task.get("held_by"):
        return None
    return sessions.read(ctx.wstore, task["held_by"])


def emit_event(store: Store, actor: Actor, kind: str, subject: str, payload: dict) -> int:
    """The watcher's own events (``session.dead``, ``task.stuck``, ``recovery.failed``): one
    ``events`` row in its own transaction, no record changes."""
    with store.tx(actor) as tx:
        return tx.event(kind, subject, payload)


# --- the task.wait_resolved handler ------------------------------------------------------------


def handle_wait_resolved(ctx: TickContext, event: Event, store: Store) -> dict[str, Any]:
    """Resume the holder of a Task whose wait the watcher (or the Orchestrator) resolved."""
    task = tasks.read(store, event.subject)
    if task["status"] != "in-progress":
        return {"skipped": f"{event.subject} is {task['status']}, not in progress"}
    if task["waiting_on"]:
        return {"skipped": f"{event.subject} waits again on {task['waiting_on']['kind']}"}
    session = holder_session(ctx, task)
    if session is None:
        return {"skipped": f"{event.subject} has no holder Session to resume"}
    kind = event.payload.get("kind", "something")
    note = event.payload.get("note") or "resolved"
    return resume_session(ctx, session, f"Your wait on {kind} resolved: {note}. Continue.")


# --- starting ----------------------------------------------------------------------------------


def _start(
    ctx: TickContext, session: dict[str, Any], text: str, key_template: str
) -> dict[str, Any]:
    session_id, profile = session["id"], session["profile"]
    store = ctx.store  # run intents live in the Coworld store; without one, starts are bare
    if store is not None:
        _close_committed_intents(store, ctx.actor, session)
    attempt = int(session["attempts"]) + 1
    for _ in range(MAX_KEY_BUMPS):
        key = key_template.format(session_id=session_id, attempt=attempt)
        intent = None
        if store is not None:
            intent = intents.begin(
                store,
                ctx.actor,
                kind="run",
                key=f"{session_id}:{attempt}",
                subject=_subject(session),
            )
            if intent["existing"] and intent["state"] == "done":
                attempt += 1  # finished without a committed set_run: never ours; skip the key
                continue
            # An existing pending intent is a crash between the start and set_run: the same
            # key makes the gateway replay that run instead of starting another.
        try:
            run_id = ctx.hermes.start(
                profile=profile, session_id=session_id, text=text, idempotency_key=key
            )
        except RunsBusy as error:
            _finish(store, ctx, intent, "failed", {"error": error.detail, "busy": True})
            return {"session": session_id, "deferred": True, "reason": error.detail}
        except RunsConflict as error:
            _finish(store, ctx, intent, "failed", {"error": error.detail, "conflict": True})
            attempt += 1
            continue
        sessions.set_run(ctx.wstore, ctx.actor, session_id, run_id, attempts=attempt)
        _finish(store, ctx, intent, "done", {"run_id": run_id})
        return {"session": session_id, "delivered": "start", "run_id": run_id, "attempt": attempt}
    return {
        "session": session_id,
        "deferred": True,
        "reason": f"{MAX_KEY_BUMPS} idempotency keys conflicted; try again next tick",
    }


def _close_committed_intents(store: Store, actor: Actor, session: dict[str, Any]) -> None:
    """Finish pending run intents whose ``set_run`` committed (a crash before ``finish``)."""
    session_id, attempts = session["id"], int(session["attempts"])
    for intent in intents.pending(store, "run"):
        owner, _, attempt_text = intent["key"].rpartition(":")
        if owner != session_id or not attempt_text.isdigit() or int(attempt_text) > attempts:
            continue
        run_id = session.get("run_id") if int(attempt_text) == attempts else None
        intents.finish(
            store,
            actor,
            _intent_number(intent),
            state="done",
            result={"run_id": run_id, "reconciled": True},
        )


def _finish(store: Store | None, ctx: TickContext, intent: dict | None, state: str, result: dict):
    if store is not None and intent is not None:
        intents.finish(store, ctx.actor, _intent_number(intent), state=state, result=result)


def _intent_number(intent: dict[str, Any]) -> int:
    return parse_display_id(intent["id"])[1]


def _subject(session: dict[str, Any]) -> str | None:
    task_id = session.get("task_id")
    return f"T-{task_id}" if task_id else None


# --- pending messages --------------------------------------------------------------------------


def _pending_message_block(ctx: TickContext, session: dict[str, Any]) -> tuple[str, list[int]]:
    """The undelivered Orchestrator messages of the Session's Task, rendered, with their ids."""
    if session.get("kind") != "task" or not session.get("task_id") or ctx.store is None:
        return "", []
    if ctx.coworld is not None and session.get("coworld_id") not in (None, ctx.coworld.coworld_id):
        return "", []
    try:
        pending = tasks.pending_messages(ctx.store, session["task_id"], "to_researcher")
    except StoreError:
        return "", []
    if not pending:
        return "", []
    lines = ["Messages from the Orchestrator while you were away:"]
    for message in pending:
        lines.append(f"- [{message['kind']}] {message['text'][:MESSAGE_LIMIT]}")
    return "\n".join(lines), [message["id"] for message in pending]
