"""Messages by steer or resume; abandon stops the run; a rejected review resumes."""

from __future__ import annotations

from research_core.store import sessions, tasks
from research_core.store.actors import Actor
from research_core.store.events import Event
from research_core.watcher import messages, recovery
from tests.watcher._support import HARNESS, events_of, make_ctx, make_task, working_session

SHA = "0123abc4567def890123abc4567def8901234567"


def send(ctx, task, text):
    tasks.message(ctx.store, HARNESS, task["id"], text=text, direction="to_researcher")


def test_messages_steer_a_live_run_and_are_marked_delivered(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    send(ctx, task, "Try seed 7.")
    send(ctx, task, "And log the episode count.")

    report = messages.run(ctx)

    assert report["steered"][0]["task"] == "T-1" and report["steered"][0]["messages"] == 2
    steered = ctx.hermes.steered[run_id][0]
    assert (
        "- [message] Try seed 7." in steered and "- [message] And log the episode count." in steered
    )
    assert tasks.pending_messages(ctx.store, 1, "to_researcher") == []
    assert messages.run(ctx)["steered"] == [] and len(ctx.hermes.runs) == 1


def test_messages_resume_a_session_without_a_live_run(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    ctx.hermes.complete(run_id)
    send(ctx, task, "Please also check the replay.")

    report = messages.run(ctx)

    assert report["resumed"][0]["delivered"] == "start"
    resumed = ctx.hermes.runs[report["resumed"][0]["run_id"]]
    assert resumed.session_id == session["id"] and "Please also check the replay." in resumed.input
    assert tasks.read(ctx.store, 1)["run_id"] == resumed.run_id
    assert tasks.pending_messages(ctx.store, 1, "to_researcher") == []


def test_messages_wait_for_the_resume_when_the_task_waits(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    tasks.wait(ctx.store, Actor.session(session["id"]), 1, kind="credits", detail="need 2")
    ctx.hermes.complete(run_id)
    send(ctx, task, "Seen later.")

    report = messages.run(ctx)

    assert report["held"] == [{"task": "T-1", "reason": "delivered when the credits wait resolves"}]
    assert (
        len(ctx.hermes.runs) == 1
        and len(tasks.pending_messages(ctx.store, 1, "to_researcher")) == 1
    )


def test_messages_are_deferred_while_the_run_is_queued(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    ctx.hermes.runs[run_id].status = "queued"
    send(ctx, task, "x")
    assert messages.run(ctx)["deferred"][0]["task"] == "T-1"
    assert len(tasks.pending_messages(ctx.store, 1, "to_researcher")) == 1


def test_abandon_stops_the_run_as_the_watchers_own_stop(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    tasks.abandon(
        ctx.store, HARNESS, 1, failure={"what_failed": "dropped by the user", "next_step": "none"}
    )
    event = Event(**events_of(ctx.store, "task.abandoned")[-1])

    result = messages.handle_abandoned(ctx, event, ctx.store)

    assert result["stopped"] == run_id and result["status"] == "stopping" and result["ended"]
    row = sessions.read(ctx.wstore, session["id"])
    assert row["stopped_by"] == "watcher:abandon" and row["ended"] is not None
    assert ctx.hermes.status(run_id, profile="researcher").status == "cancelled"
    assert recovery.run(ctx)["dead"] == [] and events_of(ctx.store, "session.dead") == []


def test_abandon_of_an_unheld_task_does_nothing(tmp_path):
    ctx = make_ctx(tmp_path)
    make_task(ctx)
    tasks.abandon(ctx.store, HARNESS, 1, failure={"what_failed": "x", "next_step": "y"})
    event = Event(**events_of(ctx.store, "task.abandoned")[-1])
    assert "not held" in messages.handle_abandoned(ctx, event, ctx.store)["skipped"]


def completed_maintenance_task(ctx):
    task = make_task(ctx, type="maintenance", title="Repair build", description="The build breaks.")
    task, session, run_id = working_session(ctx, task=task)
    tasks.complete(
        ctx.store,
        Actor.session(session["id"]),
        1,
        checklist={"commit": SHA, "summary": "fixed the toolchain path"},
        commit=SHA,
        merge_request="no",
        merge_reason="nothing to merge",
        result="the build passes again",
    )
    ctx.hermes.complete(run_id, "done")
    return task, session


def test_rejected_review_resumes_the_researcher_with_the_reason(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session = completed_maintenance_task(ctx)
    tasks.review(ctx.store, HARNESS, 1, accept=False, merge="no", reason="the check was not rerun")
    event = Event(**events_of(ctx.store, "task.reviewed")[-1])

    result = messages.handle_reviewed(ctx, event, ctx.store)

    assert result["delivered"] == "start"
    resumed = ctx.hermes.runs[result["run_id"]]
    assert resumed.session_id == session["id"]
    assert resumed.input.startswith("Review reopened T-1: the check was not rerun. Continue.")
    assert "- [message] Reopened: the check was not rerun" in resumed.input
    assert tasks.read(ctx.store, 1)["status"] == "in-progress"
    assert messages.handle_reviewed(ctx, event, ctx.store)["skipped"].startswith("the reopen")
    assert len(ctx.hermes.runs) == 2


def test_rejected_review_after_the_messages_step_delivered_the_reopen_is_a_no_op(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session = completed_maintenance_task(ctx)
    tasks.review(ctx.store, HARNESS, 1, accept=False, merge="no", reason="missing summary")
    event = Event(**events_of(ctx.store, "task.reviewed")[-1])
    assert messages.run(ctx)["resumed"][0]["task"] == "T-1"
    assert "already delivered" in messages.handle_reviewed(ctx, event, ctx.store)["skipped"]
    assert len(ctx.hermes.runs) == 2


def test_accepted_review_is_left_to_the_merge_step(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session = completed_maintenance_task(ctx)
    tasks.review(ctx.store, HARNESS, 1, accept=True, merge="no", reason="fine as is")
    event = Event(**events_of(ctx.store, "task.reviewed")[-1])
    assert "merge" in messages.handle_reviewed(ctx, event, ctx.store)["skipped"]
    assert len(ctx.hermes.runs) == 1
