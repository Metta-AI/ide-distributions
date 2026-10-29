"""Step 11: dispatch pending wakes and run the no-model handlers (design B.13, Appendix H).

For every unhandled event whose kind is in :data:`HANDLERS`, in the workspace store and the
active Coworld's store:

* a *no-model handler* is called at once with ``(ctx, event, store)``; returning acknowledges
  the event (``handled_events.handled_at``), raising records the error and leaves the event
  for the next tick; a handler whose module is not built yet is noted and left alone;
* a *model wake* starts an Orchestrator Session through the runs API. The event is claimed
  first (``attempts`` counts the tries, A2.1), a ``run`` intent is written to the Coworld store
  (A5.2), then ``hermes.start`` runs with the idempotency key ``event:<store>:<seq>:<attempt>``
  (A5.1) and the run id is recorded on the Session and the intent. The event is acknowledged
  only when a later tick sees the run ``completed`` (:func:`acknowledge_finished_wakes`); a run
  that ends any other way leaves the event unhandled with the error, and the next tick tries
  again with a new Session id and the next attempt number, up to ``recovery.max_attempts``
  tries. After that the watcher gives up on the event: ``wake.failed`` (itself a management
  wake) and a notice, once.

Repeat suppression: an event whose ``(kind, subject)`` already has a live wake Session started
within :data:`REPEAT_WINDOW` joins that Session instead of starting another, and the Session's
end acknowledges every event it carries. A gateway that answers busy leaves the claimed
Session without a run; the next tick retries the start with the same key, which the gateway
replays if the earlier request did reach it, so a crash between the start and its commit
never starts a second run (the reconciliation A5.2 asks for).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from research_core.store import events, intents, notices, sessions, settings
from research_core.store._common import parse_display_id, parse_iso
from research_core.store.db import Store
from research_core.store.events import Event
from research_core.watcher import hermes_api
from research_core.watcher.context import TickContext
from research_core.watcher.handlers import HANDLERS, MODEL_KINDS, Handler
from research_core.watcher.hermes_api import RunsBusy, RunsError
from research_core.watcher.store_checks import labelled_stores

TEMPLATES_DIR = Path(__file__).parent / "wakes"
REPEAT_WINDOW = timedelta(hours=6)
PAYLOAD_CHARS = 4000
ERROR_CHARS = 2000

WAKE_INSTRUCTIONS = (
    "You are the Orchestrator in a background wake Session that the watcher started, not a "
    "person: nobody reads this conversation. Act on the event below with the record and "
    "platform tools, read the records before you write, and finish within this run. Never "
    "write into a user conversation; when the user should hear something call ui.notify, and "
    "ui.ask when only they can decide. Settings bound your own initiative, never a request "
    "the user already made."
)


def run(ctx: TickContext) -> dict[str, Any]:
    acknowledged = acknowledge_finished_wakes(ctx)
    report: dict[str, Any] = {
        "acknowledged": acknowledged["handled"],
        "run_failed": acknowledged["failed"],
        "handled": [],
        "not_built": [],
        "started": [],
        "joined": [],
        "waiting": [],
        "busy": [],
        "failed": [],
        "gave_up": [],
        "errors": [],
    }
    max_attempts = int(settings.get(ctx.wstore, "recovery.max_attempts"))
    for label, store in labelled_stores(ctx):
        for event in events.unhandled(store, set(HANDLERS)):
            handler = HANDLERS[event.kind]
            ref = f"{label}:{event.seq}"
            try:
                if handler.model:
                    _dispatch_wake(ctx, label, store, event, handler, max_attempts, report)
                else:
                    _handle_without_model(ctx, store, event, handler, ref, report)
            except Exception as error:  # one bad event never blocks the others
                report["errors"].append(f"{ref} {event.kind}: {_describe(error)}")
    return report


def acknowledge_finished_wakes(ctx: TickContext) -> dict[str, list[str]]:
    """Settle claimed events whose wake run ended: ``completed`` acknowledges, the rest fail.

    Looks at ``handled_events`` rows that name a wake Session with a run and no ``handled_at``,
    in both stores, and asks the gateway once per run. A run the gateway no longer knows
    (``unknown``) counts as failed, so the event is retried.
    """
    report: dict[str, list[str]] = {"handled": [], "failed": []}
    statuses: dict[str, hermes_api.RunStatus] = {}
    for label, store in labelled_stores(ctx):
        rows = store.q(
            "SELECT seq, session_id FROM handled_events"
            " WHERE handled_at IS NULL AND session_id IS NOT NULL ORDER BY seq"
        )
        for row in rows:
            session = sessions.read(ctx.wstore, row["session_id"])
            if session is None or session["kind"] != "wake" or not session["run_id"]:
                continue
            run_id = session["run_id"]
            if run_id not in statuses:
                statuses[run_id] = ctx.hermes.status(run_id, profile=session["profile"])
            status = statuses[run_id]
            ref = f"{label}:{row['seq']}"
            if status.status == "completed":
                events.mark_handled(store, ctx.actor, row["seq"], run_id)
                sessions.end(ctx.wstore, ctx.actor, session["id"])
                report["handled"].append(ref)
            elif status.terminal:
                detail = status.error or "no detail"
                events.mark_failed(
                    store, ctx.actor, row["seq"], f"run {run_id} {status.status}: {detail}"
                )
                sessions.end(ctx.wstore, ctx.actor, session["id"])
                report["failed"].append(ref)
    return report


def has_queued_wakes(ctx: TickContext) -> bool:
    """Whether any model-wake event is still unacknowledged and not given up on."""
    max_attempts = int(settings.get(ctx.wstore, "recovery.max_attempts"))
    for _label, store in labelled_stores(ctx):
        for event in events.unhandled(store, MODEL_KINDS):
            row = _handled_row(store, event.seq)
            if row is None or row["attempts"] <= max_attempts:
                return True
    return False


def render_wake(kind: str, event: Event, ctx: TickContext) -> str:
    """The wake prompt: ``wakes/<kind>.md`` with the event filled in."""
    template = (TEMPLATES_DIR / f"{kind}.md").read_text(encoding="utf-8")
    payload = json.dumps(event.payload, indent=2, sort_keys=True, ensure_ascii=False)
    if len(payload) > PAYLOAD_CHARS:
        payload = payload[:PAYLOAD_CHARS] + "\n… (payload cut here; the event has the rest)"
    coworld_id = ctx.coworld.coworld_id if ctx.coworld is not None else "none active"
    replacements = (
        ("{event_kind}", event.kind),
        ("{subject}", event.subject),
        ("{payload_json}", payload),
        ("{coworld_id}", coworld_id),
    )
    text = template
    for placeholder, value in replacements:
        text = text.replace(placeholder, value)
    return text


# --- no-model handlers -----------------------------------------------------------------------


def _handle_without_model(
    ctx: TickContext,
    store: Store,
    event: Event,
    handler: Handler,
    ref: str,
    report: dict[str, Any],
) -> None:
    try:
        handle = handler.resolve()
    except (ModuleNotFoundError, AttributeError) as error:
        missing_module = isinstance(error, ModuleNotFoundError) and (
            error.name == handler.module_name
        )
        if not missing_module and not isinstance(error, AttributeError):
            raise
        report["not_built"].append(ref)
        return
    try:
        handle(ctx, event, store)
    except Exception as error:
        events.mark_failed(store, ctx.actor, event.seq, _describe(error))
        report["failed"].append(ref)
        return
    events.mark_handled(store, ctx.actor, event.seq, None)
    report["handled"].append(ref)


# --- model wakes -----------------------------------------------------------------------------


def _dispatch_wake(
    ctx: TickContext,
    label: str,
    store: Store,
    event: Event,
    handler: Handler,
    max_attempts: int,
    report: dict[str, Any],
) -> None:
    ref = f"{label}:{event.seq}"
    row = _handled_row(store, event.seq)
    if row is not None and row["attempts"] > max_attempts:
        report["gave_up"].append(ref)  # reported when it happened; nothing more to do
        return
    if row is not None and row["session_id"]:
        session = sessions.read(ctx.wstore, row["session_id"])
        if session is not None and session["ended"] is None:
            if session["run_id"]:
                report["waiting"].append(ref)  # the run is live; acknowledged when it ends
            else:
                _start(ctx, label, store, event, handler, session, row["attempts"], report)
            return
    open_session = _open_wake_session(ctx, store, event)
    if open_session is not None:
        events.claim_handling(store, ctx.actor, event.seq, event.kind, open_session["id"])
        report["joined"].append(f"{ref}->{open_session['id']}")
        return
    session_id = hermes_api.mint_session_id()
    attempts = events.claim_handling(store, ctx.actor, event.seq, event.kind, session_id)
    if attempts > max_attempts:
        _give_up(ctx, label, store, event, handler, row, max_attempts)
        report["gave_up"].append(ref)
        return
    session = sessions.register(
        ctx.wstore,
        ctx.actor,
        session_id=session_id,
        profile=ctx.orchestrator_profile,
        kind="wake",
        coworld_id=ctx.coworld.coworld_id if ctx.coworld is not None else None,
        event=event.kind,
        **_subject_ids(event.subject),
    )
    _start(ctx, label, store, event, handler, session, attempts, report)


def _start(
    ctx: TickContext,
    label: str,
    store: Store,
    event: Event,
    handler: Handler,
    session: dict[str, Any],
    attempts: int,
    report: dict[str, Any],
) -> None:
    """Record the run intent, start the run, record the run id (B.13 "uncertain outcomes")."""
    session_id = session["id"]
    ref = f"{label}:{event.seq}"
    intent = _begin_run_intent(ctx, session_id, attempts)
    try:
        run_id = ctx.hermes.start(
            profile=ctx.orchestrator_profile,
            session_id=session_id,
            text=render_wake(handler.wake, event, ctx),
            instructions=WAKE_INSTRUCTIONS,
            idempotency_key=f"event:{label}:{event.seq}:{attempts}",
        )
    except RunsBusy as error:
        ctx.note(f"wake for {ref} {event.kind} deferred to the next tick: {error}")
        report["busy"].append(ref)
        return
    except RunsError as error:  # a conflict, a refused key, another definite answer
        message = _describe(error)
        events.mark_failed(store, ctx.actor, event.seq, message)
        _finish_run_intent(ctx, intent, "failed", {"error": message})
        sessions.end(ctx.wstore, ctx.actor, session_id)
        report["failed"].append(ref)
        return
    sessions.set_run(ctx.wstore, ctx.actor, session_id, run_id)
    _finish_run_intent(ctx, intent, "done", {"run_id": run_id})
    report["started"].append(f"{ref}->{run_id}")


def _give_up(
    ctx: TickContext,
    label: str,
    store: Store,
    event: Event,
    handler: Handler,
    row: Any,
    max_attempts: int,
) -> None:
    """Stop retrying: record the failure, emit ``wake.failed`` once and tell the user."""
    last_error = None if row is None else row["last_error"]
    message = f"gave up after {max_attempts} failed wake run(s)"
    if last_error:
        message += f"; last error: {last_error}"
    events.mark_failed(store, ctx.actor, event.seq, message[:ERROR_CHARS])
    # A wake.failed about a wake.failed would chain for ever: that one ends in the notice.
    if event.kind != "wake.failed":
        with ctx.wstore.tx(ctx.actor) as tx:
            tx.event(
                "wake.failed",
                f"{label}:{event.seq}",
                {
                    "store": label,
                    "seq": event.seq,
                    "kind": event.kind,
                    "subject": event.subject,
                    "wake": handler.wake,
                    "attempts": max_attempts,
                    "last_error": last_error,
                },
            )
    refs = [event.subject] if _is_record_ref(event.subject) else []
    notices.add(
        ctx.wstore,
        ctx.actor,
        text=(
            f"The watcher could not complete the {handler.wake} wake for {event.kind}"
            f" ({event.subject}) after {max_attempts} attempt(s)"
            + (f": {last_error}" if last_error else "")
            + ". The event is left for the Orchestrator; workspace.doctor shows the runs API"
            " health."
        ),
        refs=refs,
        urgency="high",
    )


def _begin_run_intent(ctx: TickContext, session_id: str, attempts: int) -> dict | None:
    """The ``run`` intent for this start; ``None`` (with a note) when no Coworld store exists."""
    if ctx.store is None:
        ctx.note(f"no active Coworld: run intent for {session_id} not recorded")
        return None
    return intents.begin(
        ctx.store, ctx.actor, kind="run", key=f"{session_id}:{attempts}", subject=session_id
    )


def _finish_run_intent(
    ctx: TickContext, intent: dict | None, state: str, result: dict[str, Any]
) -> None:
    if intent is None or intent["state"] != "pending" or ctx.store is None:
        return
    _, intent_id = parse_display_id(intent["id"])
    intents.finish(ctx.store, ctx.actor, intent_id, state=state, result=result)


def _open_wake_session(ctx: TickContext, store: Store, event: Event) -> dict | None:
    """A live wake Session started within the window for another event of the same kind
    and subject, or ``None``."""
    rows = store.q(
        "SELECT DISTINCT h.session_id FROM handled_events h JOIN events e ON e.seq = h.seq"
        " WHERE e.kind = ? AND e.subject = ? AND h.seq != ?"
        " AND h.session_id IS NOT NULL AND h.handled_at IS NULL ORDER BY h.seq DESC",
        (event.kind, event.subject, event.seq),
    )
    for row in rows:
        session = sessions.read(ctx.wstore, row["session_id"])
        if session is None or session["kind"] != "wake" or session["ended"] is not None:
            continue
        if ctx.now - parse_iso(session["started"]) <= REPEAT_WINDOW:
            return session
    return None


def _handled_row(store: Store, seq: int) -> Any:
    return store.one("SELECT * FROM handled_events WHERE seq = ?", (seq,))


def _subject_ids(subject: str) -> dict[str, int]:
    """``task_id`` or ``goal_id`` for the Session row when the subject is such a record."""
    try:
        kind, number = parse_display_id(subject)
    except ValueError:
        return {}
    if kind == "task":
        return {"task_id": number}
    if kind == "goal":
        return {"goal_id": number}
    return {}


def _is_record_ref(subject: str) -> bool:
    try:
        parse_display_id(subject)
    except ValueError:
        return False
    return True


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:ERROR_CHARS]
