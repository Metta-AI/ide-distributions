"""Step 8: ideation and park events once per Goal while the condition holds; anti-stall daily."""

from __future__ import annotations

from datetime import UTC, datetime

from research_core.store import tasks
from research_core.watcher import goals
from tests.watcher._support import (
    FakeClock,
    advance,
    events_of,
    make_ctx,
    make_goal,
    make_task,
    set_setting,
    task_number,
)

FAILURE = {"what_failed": "dropped", "next_step": "nothing"}


def test_ideation_event_once_per_goal_until_it_has_an_open_task(tmp_path):
    ctx = make_ctx(tmp_path)
    goal = make_goal(ctx)
    other = make_goal(ctx, "Beat the house")
    make_task(ctx, other)  # the other Goal has an open Task: no event for it

    first = goals.run(ctx)
    assert first["needs_ideation"] == [f"G-{goal}"]
    assert goals.run(ctx)["needs_ideation"] == []
    assert [e["subject"] for e in events_of(ctx.store, "goal.needs_ideation")] == [f"G-{goal}"]

    task = make_task(ctx, goal)  # the wake created a Task: the condition clears
    assert goals.run(ctx)["needs_ideation"] == []
    tasks.abandon(ctx.store, ctx.actor, task_number(task), failure=FAILURE)
    assert goals.run(ctx)["needs_ideation"] == [f"G-{goal}"]  # empty again: a new event
    assert len(events_of(ctx.store, "goal.needs_ideation")) == 2


def test_park_after_three_ideation_rounds_without_progress(tmp_path):
    ctx = make_ctx(tmp_path)
    goal = make_goal(ctx)
    complete_ideation_rounds(ctx, goal, 2)
    report = goals.run(ctx)
    assert report["park"] == [] and report["needs_ideation"] == [f"G-{goal}"]

    complete_ideation_rounds(ctx, goal, 1, start=3)
    report = goals.run(ctx)
    assert report["park"] == [f"G-{goal}"]
    assert goals.run(ctx)["park"] == []
    [event] = events_of(ctx.store, "goal.park")
    assert event["payload"] == {
        "goal": f"G-{goal}",
        "title": "Reach division 1",
        "rounds_without_progress": 3,
        "park_after_rounds": 3,
    }
    # A Goal due for parking is not also asked to ideate.
    assert len(events_of(ctx.store, "goal.needs_ideation")) == 1


def test_park_threshold_follows_the_setting(tmp_path):
    ctx = make_ctx(tmp_path)
    goal = make_goal(ctx)
    set_setting(ctx, "ideation.park_after_rounds", 1)
    complete_ideation_rounds(ctx, goal, 1)
    assert goals.run(ctx)["park"] == [f"G-{goal}"]


def test_anti_stall_fires_once_a_day_after_the_local_hour(tmp_path):
    # 20:00 UTC on 2026-10-28 is 13:00 PDT: the first tick only records the day.
    clock = FakeClock(datetime(2026, 10, 28, 20, 0, tzinfo=UTC))
    ctx = make_ctx(tmp_path, clock=clock)
    assert goals.run(ctx)["anti_stall"] is False
    assert ctx.meta("anti_stall_last") == "2026-10-28"
    advance(ctx, 13 * 3600)  # the 29th, 02:00 PDT: before the default 04:00
    assert goals.run(ctx)["anti_stall"] is False
    advance(ctx, 3 * 3600)  # 05:00 PDT
    assert goals.run(ctx)["anti_stall"] is True
    assert goals.run(ctx)["anti_stall"] is False
    advance(ctx, 10 * 3600)  # still the 29th locally
    assert goals.run(ctx)["anti_stall"] is False
    advance(ctx, 14 * 3600)  # the 30th, 05:00 PDT
    assert goals.run(ctx)["anti_stall"] is True
    events = events_of(ctx.wstore, "anti_stall")
    assert [e["payload"]["date"] for e in events] == ["2026-10-29", "2026-10-30"]
    assert events[0]["payload"]["hour"] == "04:00"
    assert ctx.meta("anti_stall_last") == "2026-10-30"


def test_anti_stall_honours_the_hour_setting(tmp_path):
    clock = FakeClock(datetime(2026, 10, 28, 19, 30, tzinfo=UTC))  # 12:30 PDT
    ctx = make_ctx(tmp_path, clock=clock)
    set_setting(ctx, "anti_stall.hour", "13:00")
    goals.run(ctx)  # the first tick records the day
    advance(ctx, 24 * 3600)  # the 29th, 12:30 PDT
    assert goals.run(ctx)["anti_stall"] is False
    advance(ctx, 1800)
    assert goals.run(ctx)["anti_stall"] is True


def test_no_active_coworld_still_runs_anti_stall(tmp_path):
    clock = FakeClock(datetime(2026, 10, 29, 20, 0, tzinfo=UTC))
    ctx = make_ctx(tmp_path, clock=clock, store=None, coworld=None)
    ctx.set_meta("anti_stall_last", "2026-10-28")
    report = goals.run(ctx)
    assert report == {"needs_ideation": [], "park": [], "anti_stall": True}


def complete_ideation_rounds(ctx, goal: int, count: int, *, start: int = 1) -> None:
    """``count`` finished ideation Tasks for the Goal, as rounds ``start`` onwards."""
    with ctx.store.tx(ctx.actor) as tx:
        for round_number in range(start, start + count):
            tx.execute(
                "INSERT INTO tasks(goal_id, type, title, description, status, outcome,"
                " ideation_round, approved_by, created, created_by, updated)"
                " VALUES (?, 'ideation', ?, 'propose work', 'complete', 'succeeded', ?,"
                " 'harness', ?, 'harness', ?)",
                (goal, f"Ideation round {round_number}", round_number, tx.at, tx.at),
            )
