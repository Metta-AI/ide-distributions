"""Dispatch: slots, priority, idempotency across ticks, drained Sessions first, RunsBusy."""

from __future__ import annotations

from research_core.store import intents, sessions, tasks
from research_core.store.events import Event
from research_core.watcher import dispatch
from tests.watcher._support import (
    events_of,
    make_ctx,
    make_task,
    set_setting,
    working_session,
)


def started_runs(ctx):
    return [run for run in ctx.hermes.runs.values()]


def test_starts_runs_for_free_slots_and_records_them(tmp_path):
    ctx = make_ctx(tmp_path)
    for n in range(3):
        make_task(ctx, title=f"Task {n}")

    report = dispatch.run(ctx)

    assert report["slots"] == 0 and len(report["started"]) == 2
    assert [s["task"] for s in report["started"]] == ["T-1", "T-2"]
    runs = started_runs(ctx)
    assert [r.idempotency_key for r in runs] == ["task:1:0:1", "task:2:0:1"]
    assert all(r.profile == "researcher" for r in runs)
    for n, run in enumerate(runs, start=1):
        session = sessions.read(ctx.wstore, run.session_id)
        assert session["kind"] == "task" and session["task_id"] == n and session["attempts"] == 1
        assert session["run_id"] == run.run_id and session["coworld_id"] == "cow_fake"
        assert tasks.read(ctx.store, n)["run_id"] == run.run_id
        assert f"task.claim {{task: T-{n}}}" in run.input
        assert str(ctx.coworld.worktree_for_task(n)) in run.input
        assert "Reach division 1" in run.input and f"Task {n - 1}" in run.input
    assert [i["state"] for i in intents.pending(ctx.store)] == []
    assert len(ctx.store.q("SELECT 1 FROM intents WHERE kind = 'run' AND state = 'done'")) == 2


def test_high_priority_and_repair_tasks_go_first(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "max_researchers", 1)
    make_task(ctx, title="Normal work")
    make_task(
        ctx,
        type="maintenance",
        priority="high",
        capability="build",
        title="Repair build",
        description="The build command fails on the new toolchain.",
    )

    report = dispatch.run(ctx)

    assert [s["task"] for s in report["started"]] == ["T-2"]
    assert "repair build" in started_runs(ctx)[0].input
    assert "workspace maintenance" in started_runs(ctx)[0].input


def test_a_live_run_is_not_started_twice_across_ticks(tmp_path):
    ctx = make_ctx(tmp_path)
    make_task(ctx)
    first = dispatch.run(ctx)
    second = dispatch.run(ctx)
    assert len(first["started"]) == 1 and second["started"] == []
    assert second["skipped"] == [{"task": "T-1", "reason": "a run is live"}]
    assert len(ctx.hermes.runs) == 1 and len(sessions.for_task(ctx.wstore, 1)) == 1


def test_a_run_that_ends_without_claiming_counts_an_attempt_then_gives_up(tmp_path):
    ctx = make_ctx(tmp_path)
    make_task(ctx)
    first = dispatch.run(ctx)["started"][0]
    ctx.hermes.complete(first["run_id"], "I did nothing")

    second = dispatch.run(ctx)

    assert second["started"][0]["session"] == first["session"], "the same Session, attempt 2"
    retry = ctx.hermes.runs[second["started"][0]["run_id"]]
    assert retry.idempotency_key == "task:1:0:2" and "attempt 2" in retry.input
    assert sessions.read(ctx.wstore, first["session"])["attempts"] == 2
    ctx.hermes.complete(retry.run_id, "still nothing")

    third = dispatch.run(ctx)
    assert third["started"] == [] and third["failed"][0]["task"] == "T-1"
    failed = events_of(ctx.store, "recovery.failed")
    assert len(failed) == 1 and failed[0]["payload"]["session"] == first["session"]
    assert failed[0]["payload"]["stage"] == "dispatch"

    fourth = dispatch.run(ctx)
    assert fourth["started"] == [] and fourth["failed"] == []
    assert len(events_of(ctx.store, "recovery.failed")) == 1, "emitted once"
    assert len(ctx.hermes.runs) == 2


def test_drained_sessions_resume_first_with_their_own_session_id(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "max_researchers", 1)
    task, session, run_id = working_session(ctx)
    sessions.set_stopped_by(ctx.wstore, ctx.actor, session["id"], "watcher:drain")
    ctx.hermes.stop(run_id, profile="researcher")
    sessions.mark_drained(ctx.wstore, ctx.actor, [session["id"]], True)
    make_task(ctx, title="Fresh work")

    report = dispatch.run(ctx)

    assert [r["session"] for r in report["resumed"]] == [session["id"]]
    assert report["started"] == [] and report["slots"] == 0
    resumed = ctx.hermes.runs[report["resumed"][0]["run_id"]]
    assert resumed.session_id == session["id"] and "paused" in resumed.input
    assert "T-1" in resumed.input and str(ctx.coworld.worktree_for_task(1)) in resumed.input
    row = sessions.read(ctx.wstore, session["id"])
    assert row["drained"] is False and row["stopped_by"] is None and row["attempts"] == 2
    assert tasks.read(ctx.store, 1)["run_id"] == resumed.run_id

    again = dispatch.run(ctx)
    assert again["resumed"] == [] and again["started"] == [], "the one slot is taken"


def test_drained_flag_is_dropped_when_the_task_moved_on(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    tasks.abandon(
        ctx.store,
        ctx.actor,
        task["id"],
        failure={"what_failed": "dropped by the user", "next_step": "nothing"},
    )
    sessions.mark_drained(ctx.wstore, ctx.actor, [session["id"]], True)

    report = dispatch.run(ctx)

    assert report["resumed"] == [] and report["skipped"][0]["session"] == session["id"]
    assert sessions.read(ctx.wstore, session["id"])["drained"] is False


def test_busy_gateway_stops_dispatch_and_the_session_starts_next_tick(tmp_path):
    ctx = make_ctx(tmp_path)
    make_task(ctx)
    make_task(ctx)
    ctx.hermes.set_busy(True)

    busy = dispatch.run(ctx)

    assert busy["started"] == [] and "429" in busy["deferred"]
    assert ctx.hermes.runs == {}
    registered = sessions.for_task(ctx.wstore, 1)
    assert len(registered) == 1 and registered[0]["run_id"] is None
    ctx.hermes.set_busy(False)

    later = dispatch.run(ctx)

    assert [s["session"] for s in later["started"]][0] == registered[0]["id"]
    assert len(later["started"]) == 2 and len(sessions.for_task(ctx.wstore, 1)) == 1
    assert ctx.hermes.runs[later["started"][0]["run_id"]].idempotency_key == "task:1:0:1"


def test_paused_workspace_starts_nothing(tmp_path):
    ctx = make_ctx(tmp_path)
    make_task(ctx)
    set_setting(ctx, "paused", True)
    assert dispatch.run(ctx) == {"skipped": "paused"}
    assert ctx.hermes.runs == {}


def test_profiles_rotate_round_robin_across_ticks(tmp_path):
    ctx = make_ctx(tmp_path, researcher_profiles=("researcher-1", "researcher-2"))
    set_setting(ctx, "max_researchers", 3)
    for _ in range(2):
        make_task(ctx)
    dispatch.run(ctx)
    make_task(ctx)
    dispatch.run(ctx)
    profiles = [run.profile for run in started_runs(ctx)]
    assert profiles == ["researcher-1", "researcher-2", "researcher-1"]
    assert ctx.meta("dispatch_rr") == "3"


def test_handle_resumed_resumes_drained_sessions(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    ctx.hermes.crash(run_id)
    sessions.mark_drained(ctx.wstore, ctx.actor, [session["id"]], True)
    make_task(ctx, title="Not started by the handler")
    event = Event(
        seq=1, at="now", actor="harness", kind="workspace.resumed", subject="ws", payload={}
    )

    report = dispatch.handle_resumed(ctx, event, ctx.wstore)

    assert [r["session"] for r in report["resumed"]] == [session["id"]]
    assert report["started"] == [] and len(ctx.hermes.runs) == 2
