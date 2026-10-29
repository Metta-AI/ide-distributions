"""Drain stops live Researcher runs the watcher's way; ``has_work`` names what keeps the
workspace awake (design Appendix H "Sleep and drain", A5.6, A5.11)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from research_core.store import sessions, tasks
from research_core.store.actors import Actor
from research_core.watcher import drain
from tests.watcher._support import (
    events_of,
    make_ctx,
    make_task,
    set_setting,
    task_number,
    working_session,
)


def no_sleep(_seconds: float) -> None:
    pass


def test_drain_stops_live_runs_marks_drained_and_sets_stopped_by(tmp_path):
    ctx = make_ctx(tmp_path)
    _task, session, run_id = working_session(ctx)
    other_task = make_task(ctx, title="Second study")
    _t2, other, other_run = working_session(ctx, "api_1761690000_aaaa0002", other_task)
    ctx.hermes.complete(other_run)  # already over: nothing to stop

    report = drain.run(ctx, sleep=no_sleep)

    assert report["stopped"] == [session["id"]] and report["quiet"] is True
    assert report["errors"] == [] and report["processes"] == []
    stopped = sessions.read(ctx.wstore, session["id"])
    assert stopped["stopped_by"] == "watcher:drain" and stopped["drained"] is True
    assert sessions.read(ctx.wstore, other["id"])["drained"] is False
    calls = [(name, kw.get("run_id")) for name, kw in ctx.hermes.calls if name == "stop"]
    assert calls == [("stop", run_id)]
    assert ctx.hermes.status(run_id, profile="researcher").status == "cancelled"
    [event] = events_of(ctx.wstore, "drain.done")
    assert event["payload"]["sessions"] == [session["id"]]
    # stopped_by was set before the stop reached the gateway (A5.6): the stop call came after.
    order = [name for name, _ in ctx.hermes.calls]
    assert order.index("stop") > 0


def test_drain_with_nothing_running_is_quiet(tmp_path):
    ctx = make_ctx(tmp_path)
    report = drain.run(ctx, sleep=no_sleep)
    assert report == {"stopped": [], "processes": [], "quiet": True, "errors": []}
    assert len(events_of(ctx.wstore, "drain.done")) == 1


def test_drain_signals_background_processes_a_task_waits_on(tmp_path):
    ctx = make_ctx(tmp_path)
    task, session, run_id = working_session(ctx)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ctx.hermes.complete(run_id)  # the Researcher ended its run to wait
        tasks.wait(
            ctx.store,
            Actor.session(session["id"]),
            task_number(task),
            kind="process",
            detail="the long evaluation",
            ref=child.pid,
        )
        report = drain.run(ctx, sleep=no_sleep)
        assert report["processes"] == [child.pid]
        assert child.wait(timeout=10) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGKILL)


def test_has_work_cases(tmp_path):
    ctx = make_ctx(tmp_path)
    assert drain.has_work(ctx) is False

    task, session, run_id = working_session(ctx)
    assert drain.has_work(ctx) is True  # a live Researcher run

    ctx.hermes.complete(run_id)
    assert drain.has_work(ctx) is False
    holder = Actor.session(session["id"])
    tasks.wait(ctx.store, holder, task_number(task), kind="question", detail="which seed?")
    assert drain.has_work(ctx) is False  # a question waits on the Orchestrator, not the platform
    tasks.wait(ctx.store, holder, task_number(task), kind="xp", detail="10 episodes", ref=["xp1"])
    assert drain.has_work(ctx) is True
    tasks.resolve_wait(ctx.store, ctx.actor, task_number(task), note="done")
    assert drain.has_work(ctx) is False

    with ctx.store.tx(ctx.actor) as tx:  # a queued management wake
        tx.event("task.asked", task["id"], {"question": "which seed?"})
    assert drain.has_work(ctx) is True


def test_has_work_autonomous_window_needs_a_candidate(tmp_path):
    ctx = make_ctx(tmp_path)
    assert drain.has_work(ctx) is False  # the window is open (until is null) but empty
    candidate_task(ctx, versions=["V-1"])
    assert drain.has_work(ctx) is True
    with ctx.store.tx(ctx.actor) as tx:
        tx.event("version.submitted", "V-1", {"league_id": "league_fake"})
    assert drain.has_work(ctx) is False
    candidate_task(ctx, versions=["V-2"])
    assert drain.has_work(ctx) is True
    set_setting(ctx, "submissions.mode", "ask")
    assert drain.has_work(ctx) is False
    set_setting(ctx, "submissions.mode", "autonomous")
    set_setting(ctx, "submissions.until", "2026-10-01T00:00:00Z")  # the window closed
    assert drain.has_work(ctx) is False


def candidate_task(ctx, *, versions: list[str]) -> None:
    """A succeeded improvement Task whose checklist names a submission candidate."""
    checklist = {"submission_candidate": "yes", "versions": versions, "claims": ["C-1"]}
    with ctx.store.tx(ctx.actor) as tx:
        goal = tx.execute(
            "INSERT INTO goals(title, end_state, reason, status, created, created_by, updated)"
            " VALUES ('win', 'champion', 'asked', 'active', ?, 'harness', ?)",
            (tx.at, tx.at),
        ).lastrowid
        tx.execute(
            "INSERT INTO tasks(goal_id, type, title, description, status, outcome, checklist,"
            " approved_by, created, created_by, updated)"
            " VALUES (?, 'improvement', 'Tune', 'tune it', 'complete', 'succeeded', ?,"
            " 'harness', ?, 'harness', ?)",
            (goal, json.dumps(checklist), tx.at, tx.at),
        )
