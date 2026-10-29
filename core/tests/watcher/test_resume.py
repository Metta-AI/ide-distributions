"""resume_session: steer a live run, else start with a reconciled run intent (A5.1–A5.3)."""

from __future__ import annotations

import json

from research_core.store import intents, sessions, tasks
from research_core.store.actors import Actor
from research_core.store.events import Event
from research_core.watcher import resume
from tests.watcher._support import (
    HARNESS,
    events_of,
    make_ctx,
    make_task,
    register_session,
    start_run,
    working_session,
)


def pending_run_intents(ctx):
    return [i["key"] for i in intents.pending(ctx.store, "run")]


def run_intents(ctx):
    return [dict(row) for row in ctx.store.q("SELECT key, state, result FROM intents ORDER BY id")]


def test_live_run_is_steered_not_restarted(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)

    result = resume.resume_session(ctx, session, "Carry on.")

    assert result == {"session": session["id"], "delivered": "steer", "run_id": run_id}
    assert ctx.hermes.steered[run_id] == ["Carry on."]
    assert len(ctx.hermes.runs) == 1
    assert pending_run_intents(ctx) == []


def test_finished_run_starts_again_on_the_same_session_id(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, first = working_session(ctx)
    ctx.hermes.complete(first, "done for now")

    result = resume.resume_session(ctx, session, "Your wait resolved. Continue.")

    assert result["delivered"] == "start" and result["run_id"] != first
    new = ctx.hermes.runs[result["run_id"]]
    assert new.session_id == session["id"] and new.profile == "researcher"
    assert new.idempotency_key == f"session:{session['id']}:2"
    row = sessions.read(ctx.wstore, session["id"])
    assert row["run_id"] == result["run_id"] and row["attempts"] == 2 and row["ended"] is None
    rows = run_intents(ctx)
    assert [(r["key"], r["state"]) for r in rows] == [(f"{session['id']}:2", "done")]
    assert json.loads(rows[0]["result"]) == {"run_id": new.run_id}
    assert len(sessions.for_task(ctx.wstore, 1)) == 1, "no new Session row"


def test_queued_or_parked_run_defers_to_the_next_tick(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    ctx.hermes.runs[run_id].status = "queued"
    queued = resume.resume_session(ctx, session, "x")
    assert queued["deferred"] is True and "refused the steer" in queued["reason"]
    ctx.hermes.runs[run_id].status = "waiting_for_approval"  # neither steerable nor over
    parked = resume.resume_session(ctx, session, "x")
    assert parked["deferred"] is True and "waiting_for_approval" in parked["reason"]
    assert len(ctx.hermes.runs) == 1 and ctx.hermes.steered == {}


def test_busy_gateway_defers_and_the_failed_intent_is_retried_next_time(tmp_path):
    ctx = make_ctx(tmp_path)
    task = make_task(ctx)
    session = register_session(ctx, "api_1_s", task)
    ctx.hermes.set_busy(True)

    deferred = resume.resume_session(ctx, session, "start")

    assert deferred["deferred"] is True and "429" in deferred["reason"]
    assert [i["state"] for i in run_intents(ctx)] == ["failed"]
    ctx.hermes.set_busy(False)
    started = resume.resume_session(ctx, sessions.read(ctx.wstore, "api_1_s"), "start")
    assert started["delivered"] == "start" and started["attempt"] == 1
    rows = run_intents(ctx)
    assert len(rows) == 1 and rows[0]["state"] == "done" and rows[0]["key"] == "api_1_s:1"


def test_crash_between_start_and_set_run_replays_the_stored_run(tmp_path):
    """The intent is pending, the gateway holds the run under the key, the Session row has no
    run id: the next start reuses the key and gets the same run back, so one run exists."""
    ctx = make_ctx(tmp_path)
    task = make_task(ctx)
    session = register_session(ctx, "api_1_s", task)
    intents.begin(ctx.store, HARNESS, kind="run", key="api_1_s:1", subject="T-1")
    text = "Start working."
    orphan = ctx.hermes.start(
        profile="researcher", session_id="api_1_s", text=text, idempotency_key="session:api_1_s:1"
    )

    result = resume.resume_session(ctx, session, text)

    assert result == {"session": "api_1_s", "delivered": "start", "run_id": orphan, "attempt": 1}
    assert len(ctx.hermes.runs) == 1
    assert sessions.read(ctx.wstore, "api_1_s")["run_id"] == orphan
    assert [i["state"] for i in run_intents(ctx)] == ["done"]


def test_crash_between_set_run_and_finish_closes_the_old_intent_first(tmp_path):
    ctx = make_ctx(tmp_path)
    task = make_task(ctx)
    session = register_session(ctx, "api_1_s", task)
    intents.begin(ctx.store, HARNESS, kind="run", key="api_1_s:1", subject="T-1")
    first = start_run(ctx, "api_1_s")  # set_run committed (attempts = 1), finish never ran
    ctx.hermes.fail(first, "boom")

    result = resume.resume_session(ctx, sessions.read(ctx.wstore, "api_1_s"), "again")

    assert result["delivered"] == "start" and result["attempt"] == 2
    rows = run_intents(ctx)
    assert [(r["key"], r["state"]) for r in rows] == [("api_1_s:1", "done"), ("api_1_s:2", "done")]
    assert first in rows[0]["result"] and "reconciled" in rows[0]["result"]
    assert session["id"] == "api_1_s"


def test_idempotency_conflict_bumps_the_attempt_once(tmp_path):
    ctx = make_ctx(tmp_path)
    task = make_task(ctx)
    session = register_session(ctx, "api_1_s", task)
    # Another body already used this key (a stale key in the gateway's 24-hour store).
    stale = ctx.hermes.start(
        profile="researcher", session_id="api_1_s", text="old", idempotency_key="session:api_1_s:1"
    )
    ctx.hermes.complete(stale)

    result = resume.resume_session(ctx, session, "new text")

    assert result["delivered"] == "start" and result["attempt"] == 2
    assert ctx.hermes.runs[result["run_id"]].idempotency_key == "session:api_1_s:2"
    assert [(r["key"], r["state"]) for r in run_intents(ctx)] == [
        ("api_1_s:1", "failed"),
        ("api_1_s:2", "done"),
    ]


def test_pending_orchestrator_messages_ride_along_and_are_marked_delivered(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    tasks.message(
        ctx.store, HARNESS, task["id"], text="Try seed 7 first.", direction="to_researcher"
    )

    result = resume.resume_session(ctx, session, "Continue.", attempt_note="(attempt 1 of 2)")

    steered = ctx.hermes.steered[run_id][0]
    assert steered.startswith("Continue.\n\nMessages from the Orchestrator while you were away:")
    assert "- [message] Try seed 7 first." in steered
    assert steered.endswith("(attempt 1 of 2)")
    assert result["messages_delivered"] == 1
    assert tasks.pending_messages(ctx.store, task["id"], "to_researcher") == []


def test_messages_stay_pending_when_the_delivery_is_deferred(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    tasks.message(ctx.store, HARNESS, task["id"], text="hello", direction="to_researcher")
    ctx.hermes.runs[run_id].status = "queued"
    assert resume.resume_session(ctx, session, "x")["deferred"] is True
    assert len(tasks.pending_messages(ctx.store, task["id"], "to_researcher")) == 1


def test_wait_resolved_handler_resumes_the_holder(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    holder = Actor.session(session["id"])
    tasks.wait(ctx.store, holder, task["id"], kind="xp", detail="2 requests", ref=["x1"])
    tasks.resolve_wait(ctx.store, HARNESS, task["id"], note="XP requests x1 completed")
    event = Event(**events_of(ctx.store, "task.wait_resolved")[-1])

    live = resume.handle_wait_resolved(ctx, event, ctx.store)
    assert live["delivered"] == "steer"
    assert ctx.hermes.steered[run_id] == [
        "Your wait on xp resolved: XP requests x1 completed. Continue."
    ]

    ctx.hermes.complete(run_id)
    tasks.wait(ctx.store, holder, task["id"], kind="credits", detail="need 3")
    tasks.resolve_wait(ctx.store, HARNESS, task["id"], note="balance 10")
    event = Event(**events_of(ctx.store, "task.wait_resolved")[-1])
    restarted = resume.handle_wait_resolved(ctx, event, ctx.store)
    assert restarted["delivered"] == "start"
    assert ctx.hermes.runs[restarted["run_id"]].session_id == session["id"]
    assert len(sessions.for_task(ctx.wstore, 1)) == 1


def test_wait_resolved_handler_skips_a_task_that_waits_again_or_is_done(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    holder = Actor.session(session["id"])
    tasks.wait(ctx.store, holder, task["id"], kind="xp", detail="x", ref=["x1"])
    tasks.resolve_wait(ctx.store, HARNESS, task["id"], note="done")
    event = Event(**events_of(ctx.store, "task.wait_resolved")[-1])
    tasks.wait(ctx.store, holder, task["id"], kind="credits", detail="need 1")
    assert "waits again" in resume.handle_wait_resolved(ctx, event, ctx.store)["skipped"]
    assert ctx.hermes.steered == {}
