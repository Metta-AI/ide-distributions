"""Run checks, dead-run recovery, replacement after max attempts, stuck Tasks."""

from __future__ import annotations

import pytest

from research_core import gitops
from research_core.store import sessions, tasks
from research_core.store.actors import Actor
from research_core.store.errors import LeaseError
from research_core.store.events import Event
from research_core.watcher import recovery
from tests.watcher._support import (
    HARNESS,
    advance,
    claim,
    events_of,
    make_ctx,
    make_task,
    set_setting,
    working_session,
)


def dead_event(ctx):
    return Event(**events_of(ctx.store, "session.dead")[-1])


def stuck_event(ctx):
    return Event(**events_of(ctx.store, "task.stuck")[-1])


def test_completed_runs_end_their_sessions_without_a_death(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    wake = sessions.register(
        ctx.wstore, HARNESS, session_id="w1", profile="orchestrator", kind="wake"
    )
    wake_run = ctx.hermes.start(profile="orchestrator", session_id="w1", text="review")
    sessions.set_run(ctx.wstore, HARNESS, "w1", wake_run)
    assert recovery.run(ctx)["ended"] == []

    ctx.hermes.complete(run_id)
    ctx.hermes.fail(wake_run, "model error")
    report = recovery.run(ctx)

    assert [e["session"] for e in report["ended"]] == [session["id"], "w1"]
    assert report["dead"] == [] and events_of(ctx.store, "session.dead") == []
    assert sessions.read(ctx.wstore, session["id"])["ended"] is not None
    assert sessions.read(ctx.wstore, "w1")["ended"] is not None and wake["kind"] == "wake"


@pytest.mark.parametrize("how", ["fail", "crash"])
def test_a_dead_run_is_resumed_on_the_same_session(tmp_path, how):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    getattr(ctx.hermes, how)(run_id, *(["boom"] if how == "fail" else []))

    report = recovery.run(ctx)

    assert report["dead"] == [{"session": session["id"], "task": "T-1"}]
    event = dead_event(ctx)
    assert event.payload["run"] == run_id and event.payload["session"] == session["id"]
    assert len(events_of(ctx.store, "session.dead")) == 1

    result = recovery.handle_dead(ctx, event, ctx.store)

    assert result["action"] == "resumed" and result["delivered"] == "start"
    resumed = ctx.hermes.runs[result["run_id"]]
    assert resumed.session_id == session["id"] and resumed.input.startswith(recovery.DEAD_TEXT)
    assert "Attempt 2 of 2" in resumed.input
    row = sessions.read(ctx.wstore, session["id"])
    assert row["attempts"] == 2 and row["ended"] is None
    assert tasks.read(ctx.store, 1)["run_id"] == resumed.run_id
    assert recovery.run(ctx)["dead"] == [], "the new run is live"


def test_watcher_initiated_stops_are_not_deaths(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    sessions.set_stopped_by(ctx.wstore, ctx.actor, session["id"], "watcher:drain")
    ctx.hermes.stop(run_id, profile="researcher")

    report = recovery.run(ctx)

    assert report["ended"][0]["status"] == "cancelled"
    assert report["skipped"] == [{"session": session["id"], "reason": "stopped by watcher:drain"}]
    assert events_of(ctx.store, "session.dead") == []


def test_a_waiting_task_or_a_finished_task_is_not_recovered(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    tasks.wait(ctx.store, Actor.session(session["id"]), 1, kind="credits", detail="need 1")
    ctx.hermes.fail(run_id, "boom")
    assert recovery.run(ctx)["dead"] == []
    assert events_of(ctx.store, "session.dead") == []


def test_dead_twice_then_replaced_with_a_fresh_session(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "recovery.max_attempts", 3)
    code = gitops.init_repo(ctx.coworld.code)
    gitops.worktree_add(code, ctx.coworld.worktree_for_task(1), branch="task/1")
    (ctx.coworld.worktree_for_task(1) / "policy.py").write_text("print('v1')\n")
    gitops.commit_all(ctx.coworld.worktree_for_task(1), "Tune the opening")
    task, session, run_id = working_session(ctx)
    old_actor = Actor.session(session["id"])
    ctx.hermes.complete(run_id, "claimed and started")

    for n in range(2):
        ctx.hermes.fail(sessions.read(ctx.wstore, session["id"])["run_id"], f"death {n + 1}")
        recovery.run(ctx)
        result = recovery.handle_dead(ctx, dead_event(ctx), ctx.store)
        assert result["action"] == "resumed", n
    assert sessions.read(ctx.wstore, session["id"])["attempts"] == 3

    ctx.hermes.fail(sessions.read(ctx.wstore, session["id"])["run_id"], "death 3")
    recovery.run(ctx)
    result = recovery.handle_dead(ctx, dead_event(ctx), ctx.store)

    assert result["action"] == "replaced" and result["old_session"] == session["id"]
    new_id = result["new_session"]
    assert new_id != session["id"] and new_id.startswith("api_")
    old = sessions.read(ctx.wstore, session["id"])
    assert old["stopped_by"] == "watcher:replace"
    new = sessions.read(ctx.wstore, new_id)
    assert new["parent"] == session["id"] and new["task_id"] == 1 and new["attempts"] == 1
    assert new["profile"] == "researcher"
    released = tasks.read(ctx.store, 1)
    assert released["status"] == "unclaimed" and released["held_by"] is None
    assert released["run_id"] == new["run_id"]
    assert events_of(ctx.store, "task.released")[-1]["payload"]["reason"].startswith(
        "replaced after 3 failed runs"
    )
    summary = ctx.hermes.runs[new["run_id"]].input
    assert "Task T-1 — Study the opening" in summary and "Tune the opening" in summary
    assert "claimed and started" in summary and "death" not in summary.split("##")[0]
    assert "task.claim {task: T-1}" in summary

    # The fresh Session claims at a higher epoch; the replaced one can no longer write.
    claimed = claim(ctx, new_id, 1)
    assert claimed["lease_epoch"] == 2 and claimed["held_by"] == new_id
    with pytest.raises(LeaseError):
        tasks.renew_lease(ctx.store, old_actor, 1)
    assert recovery.run(ctx)["dead"] == []


def test_replacement_that_cannot_start_raises_recovery_failed(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "recovery.max_attempts", 1)
    task, session, run_id = working_session(ctx)
    ctx.hermes.fail(run_id, "boom")
    recovery.run(ctx)
    ctx.hermes.set_busy(True)

    result = recovery.handle_dead(ctx, dead_event(ctx), ctx.store)

    assert result["action"] == "replace_failed" and "429" in result["reason"]
    failed = events_of(ctx.store, "recovery.failed")[-1]["payload"]
    assert failed["task"] == "T-1" and failed["old_session"] == session["id"]
    assert failed["session"] == result["new_session"] and failed["stage"] == "replace"
    assert tasks.read(ctx.store, 1)["status"] == "unclaimed", "dispatch picks it up next tick"


def test_handle_dead_skips_a_session_that_is_live_again(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    ctx.hermes.fail(run_id, "boom")
    recovery.run(ctx)
    event = dead_event(ctx)
    first = recovery.handle_dead(ctx, event, ctx.store)
    second = recovery.handle_dead(ctx, event, ctx.store)
    assert first["action"] == "resumed" and "live run again" in second["skipped"]
    assert len(ctx.hermes.runs) == 2


def test_stuck_task_is_flagged_once_per_stale_lease_and_recovered(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    ctx.hermes.complete(run_id, "ended my run without waiting")
    recovery.run(ctx)
    advance(ctx, 299)
    assert recovery.run(ctx)["stuck"] == []
    advance(ctx, 2)

    report = recovery.run(ctx)

    assert report["stuck"] == [{"task": "T-1", "session": session["id"]}]
    assert recovery.run(ctx)["stuck"] == [], "flagged once"
    assert len(events_of(ctx.store, "task.stuck")) == 1
    event = stuck_event(ctx)
    assert event.payload["run"] == run_id

    result = recovery.handle_stuck(ctx, event, ctx.store)

    assert result["action"] == "resumed"
    assert ctx.hermes.runs[result["run_id"]].session_id == session["id"]
    assert recovery.run(ctx)["stuck"] == [], "a live run is never stuck"


def test_a_live_run_with_a_stale_lease_is_not_stuck(tmp_path):
    ctx = make_ctx(tmp_path)
    working_session(ctx)
    advance(ctx, 600)
    assert recovery.run(ctx)["stuck"] == []
    assert events_of(ctx.store, "task.stuck") == []


def test_drained_sessions_are_not_stuck(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    sessions.set_stopped_by(ctx.wstore, ctx.actor, session["id"], "watcher:drain")
    ctx.hermes.stop(run_id, profile="researcher")
    sessions.mark_drained(ctx.wstore, ctx.actor, [session["id"]], True)
    advance(ctx, 600)
    assert recovery.run(ctx)["stuck"] == []


def test_unclaimed_tasks_never_look_stuck(tmp_path):
    ctx = make_ctx(tmp_path)
    make_task(ctx)
    advance(ctx, 600)
    assert recovery.run(ctx)["stuck"] == []
