"""Goals: creation, updates, status transitions, computed reads and the ideation rules.

Rows in the tasks, experiments, claims and versions tables are inserted directly here: those
modules are separate tasks and goals.read queries the tables, not the modules.
"""

from __future__ import annotations

import pytest

from research_core.store import directives, goals
from research_core.store._common import json_field
from research_core.store.errors import NotFound, StoreError

START = "2026-10-28T21:04:05.123456Z"
PLUS_60 = "2026-10-28T21:05:05.123456Z"


def make_goal(store, actor, **overrides):
    fields = {"title": "Beat the starter", "end_state": "A positive margin", "reason": "asked"}
    fields.update(overrides)
    return goals.create(store, actor, **fields)


def insert_task(
    store,
    actor,
    *,
    goal_id,
    type="research",
    status="unclaimed",
    outcome=None,
    ideation_round=None,
    waiting_on=None,
) -> int:
    failure = json_field({"what_failed": "x", "next_step": "y"}) if status == "abandoned" else None
    with store.tx(actor) as tx:
        cursor = tx.execute(
            "INSERT INTO tasks(goal_id, type, title, description, status, outcome, failure,"
            " ideation_round, waiting_on, created, created_by, updated)"
            " VALUES (?, ?, 't', 'd', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                goal_id,
                type,
                status,
                outcome,
                failure,
                ideation_round,
                json_field(waiting_on) if waiting_on else None,
                tx.at,
                str(actor),
                tx.at,
            ),
        )
        return cursor.lastrowid


def insert_experiment(store, actor, *, goal_id, status) -> int:
    with store.tx(actor) as tx:
        cursor = tx.execute(
            "INSERT INTO experiments(summary, goal_id, status, created, created_by)"
            " VALUES ('s', ?, ?, ?, ?)",
            (goal_id, status, tx.at, str(actor)),
        )
        return cursor.lastrowid


def insert_claim(store, actor, *, tags, evidence=()) -> int:
    """A claim with ``evidence`` as (stance, added) pairs; ``added`` None means now."""
    with store.tx(actor) as tx:
        cursor = tx.execute(
            "INSERT INTO claims(summary, tags, status, created, created_by, updated)"
            " VALUES ('s', ?, 'active', ?, ?, ?)",
            (json_field(tags), tx.at, str(actor), tx.at),
        )
        claim_id = cursor.lastrowid
        for stance, added in evidence:
            tx.execute(
                'INSERT INTO claim_evidence(claim_id, type, ref, stance, added, "by")'
                " VALUES (?, 'code', 'a.py:1-2@abcdef1', ?, ?, ?)",
                (claim_id, stance, added or tx.at, str(actor)),
            )
        return claim_id


def insert_version(store, actor, *, task_id=None, experiment_id=None) -> int:
    with store.tx(actor) as tx:
        cursor = tx.execute(
            "INSERT INTO versions(summary, source, task_id, experiment_id, created, created_by)"
            " VALUES ('v', 'adopted', ?, ?, ?, ?)",
            (task_id, experiment_id, tx.at, str(actor)),
        )
        return cursor.lastrowid


# --- create -----------------------------------------------------------------------------------


def test_create_returns_the_record_and_emits_goal_created(coworld_store, session_actor):
    goal = make_goal(coworld_store, session_actor, tags=["speed"], judgement="Compare margins")
    assert goal["id"] == "G-1"
    assert goal["status"] == "active"
    assert goal["status_reason"] is None
    assert goal["tags"] == ["speed"]
    assert goal["judgement"] == "Compare margins"
    assert goal["description"] == "" and goal["where_it_stands"] == ""
    assert goal["updates_for_human"] == ""
    assert goal["created"] == START and goal["updated"] == START
    assert goal["created_by"] == str(session_actor)
    assert goal["tasks"]["open"] == [] and goal["claims"] == [] and goal["directives"] == []
    assert goal["ideation_rounds_without_progress"] == 0 and goal["last_progress"] is None
    events = coworld_store.events_after(0)
    assert [(e.kind, e.subject, e.actor) for e in events] == [
        ("goal.created", "G-1", str(session_actor))
    ]
    assert events[0].payload == {"title": "Beat the starter"}
    assert coworld_store.unmarked_changes() == []


def test_create_reports_every_problem_at_once(coworld_store, harness):
    with pytest.raises(StoreError) as info:
        goals.create(coworld_store, harness, title="", end_state="  ", reason="", tags="speed")
    problems = info.value.problems
    assert len(problems) == 4, problems
    for field in ("title", "end_state", "reason", "tags"):
        assert any(p.startswith(field) for p in problems), (field, problems)
    with pytest.raises(StoreError) as info:
        make_goal(coworld_store, harness, title="x" * 201, tags=["ok", ""])
    assert len(info.value.problems) == 2, info.value.problems
    assert coworld_store.last_seq() == 0
    assert coworld_store.q("SELECT id FROM goals") == []


# --- update -----------------------------------------------------------------------------------


def test_update_replaces_fields_and_appends_notes_for_the_user(coworld_store, harness, clock):
    make_goal(coworld_store, harness)
    clock.advance(60)
    goal = goals.update(
        coworld_store,
        harness,
        1,
        where_it_stands="Nothing measured yet",
        update_for_human="Started looking at the starter.",
        tags=("speed", "starter"),
    )
    assert goal["where_it_stands"] == "Nothing measured yet"
    assert goal["updates_for_human"] == f"[{PLUS_60}] Started looking at the starter."
    assert goal["tags"] == ["speed", "starter"]
    assert goal["updated"] == PLUS_60 and goal["created"] == START
    clock.advance(60)
    goal = goals.update(
        coworld_store, harness, 1, where_it_stands="One lead", update_for_human="Found a lead."
    )
    assert goal["where_it_stands"] == "One lead"
    assert goal["updates_for_human"] == (
        f"[{PLUS_60}] Started looking at the starter.\n\n"
        "[2026-10-28T21:06:05.123456Z] Found a lead."
    )
    events = coworld_store.events_after(1)
    assert [e.kind for e in events] == ["goal.updated", "goal.updated"]
    assert events[0].payload == {"fields": ["tags", "update_for_human", "where_it_stands"]}
    assert events[0].subject == "G-1"


def test_update_reports_every_problem_at_once(coworld_store, harness):
    make_goal(coworld_store, harness)
    with pytest.raises(StoreError) as info:
        goals.update(coworld_store, harness, 1, title="", colour="red", tags=[1])
    problems = info.value.problems
    assert len(problems) == 3, problems
    assert any("colour" in p for p in problems)
    with pytest.raises(StoreError) as info:
        goals.update(coworld_store, harness, 1)
    assert len(info.value.problems) == 1
    with pytest.raises(NotFound):
        goals.update(coworld_store, harness, 9, title="x")
    assert coworld_store.last_seq() == 1


# --- status -----------------------------------------------------------------------------------


def test_set_status_follows_the_transitions(coworld_store, harness, clock):
    make_goal(coworld_store, harness)
    clock.advance(60)
    goal = goals.set_status(coworld_store, harness, 1, "parked", "three rounds, no progress")
    assert goal["status"] == "parked"
    assert goal["status_reason"] == "three rounds, no progress"
    assert goal["updated"] == PLUS_60
    event = coworld_store.events_after(1)[0]
    assert event.kind == "goal.status" and event.subject == "G-1"
    assert event.payload == {
        "from": "active",
        "to": "parked",
        "reason": "three rounds, no progress",
    }
    assert goals.set_status(coworld_store, harness, 1, "active", "user resumed")["status"] == (
        "active"
    )
    assert goals.set_status(coworld_store, harness, 1, "completed", "done")["status"] == (
        "completed"
    )
    with pytest.raises(StoreError) as info:
        goals.set_status(coworld_store, harness, 1, "active", "again")
    assert info.value.problems == ["G-1 is completed; it cannot become active"]
    # parked goals cannot complete without passing through active
    make_goal(coworld_store, harness)
    goals.set_status(coworld_store, harness, 2, "parked", "wait")
    with pytest.raises(StoreError) as info:
        goals.set_status(coworld_store, harness, 2, "completed", "done")
    assert info.value.problems == ["G-2 is parked; it can become active or abandoned"]


def test_set_status_reports_every_problem_at_once(coworld_store, harness):
    make_goal(coworld_store, harness)
    goals.set_status(coworld_store, harness, 1, "completed", "done")
    with pytest.raises(StoreError) as info:
        goals.set_status(coworld_store, harness, 1, "completed", "")
    problems = info.value.problems
    assert len(problems) == 2, problems
    assert any(p.startswith("status_reason") for p in problems)
    with pytest.raises(StoreError) as info:
        goals.set_status(coworld_store, harness, 1, "done", "x")
    assert len(info.value.problems) == 1 and "done" in info.value.problems[0]
    with pytest.raises(NotFound):
        goals.set_status(coworld_store, harness, 7, "parked", "x")


# --- read: computed fields ---------------------------------------------------------------------


def test_read_counts_tasks_and_experiments_by_status(coworld_store, harness):
    make_goal(coworld_store, harness)
    make_goal(coworld_store, harness, title="Other")
    insert_task(coworld_store, harness, goal_id=1, status="unclaimed")
    insert_task(coworld_store, harness, goal_id=1, status="in-progress")
    insert_task(coworld_store, harness, goal_id=1, status="complete", outcome="succeeded")
    insert_task(coworld_store, harness, goal_id=1, status="abandoned", outcome="abandoned")
    insert_task(coworld_store, harness, goal_id=2, status="proposed")
    for status in ("proposed", "running", "running"):
        insert_experiment(coworld_store, harness, goal_id=1, status=status)
    insert_experiment(coworld_store, harness, goal_id=2, status="confirmed")
    goal = goals.read(coworld_store, 1)
    assert goal["tasks"] == {
        "proposed": 0,
        "unclaimed": 1,
        "in-progress": 1,
        "complete": 1,
        "abandoned": 1,
        "open": ["T-1", "T-2"],
    }
    assert goal["experiments"] == {
        "proposed": 1,
        "running": 2,
        "confirmed": 0,
        "refuted": 0,
        "inconclusive": 0,
        "abandoned": 0,
    }
    other = goals.read(coworld_store, 2)
    assert other["tasks"]["proposed"] == 1 and other["tasks"]["open"] == ["T-5"]
    assert other["experiments"]["confirmed"] == 1


def test_read_lists_claims_tagged_with_the_goal_with_freshness(coworld_store, harness, clock):
    make_goal(coworld_store, harness)
    t0 = START
    clock.advance(60)
    t1 = PLUS_60
    clock.advance(60)
    t2 = "2026-10-28T21:06:05.123456Z"
    insert_claim(
        coworld_store,
        harness,
        tags=["G-1", "speed"],
        evidence=[("supports", t0), ("qualifies", t1), ("contradicts", t2)],
    )
    insert_claim(coworld_store, harness, tags=["G-2"], evidence=[("supports", t2)])
    insert_claim(coworld_store, harness, tags=["G-1"])
    insert_claim(coworld_store, harness, tags=["G-10"], evidence=[("supports", t2)])
    goal = goals.read(coworld_store, 1)
    assert goal["claims"] == [
        {"id": "C-1", "summary": "s", "status": "active", "freshness": t1},
        {"id": "C-3", "summary": "s", "status": "active", "freshness": None},
    ]


def test_read_computes_last_progress_and_ideation_rounds(coworld_store, harness, clock):
    make_goal(coworld_store, harness)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=1,
    )  # fmt: skip
    clock.advance(60)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=2,
    )  # fmt: skip
    goal = goals.read(coworld_store, 1)
    # Ideation rounds completing is not progress, even with outcome succeeded.
    assert goal["last_progress"] is None
    assert goal["ideation_rounds_without_progress"] == 2

    clock.advance(60)
    progress_at = "2026-10-28T21:06:05.123456Z"
    research = insert_task(
        coworld_store, harness, goal_id=1, status="complete", outcome="succeeded"
    )
    goal = goals.read(coworld_store, 1)
    assert goal["last_progress"] == progress_at
    assert goal["ideation_rounds_without_progress"] == 0

    clock.advance(60)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=3,
    )  # fmt: skip
    clock.advance(60)
    # An open ideation round and an abandoned one are not rounds that brought no progress.
    insert_task(coworld_store, harness, goal_id=1, type="ideation", status="in-progress")
    insert_task(coworld_store, harness, goal_id=1, type="ideation", status="abandoned")
    # A failed research Task and one under another Goal are not progress either.
    insert_task(coworld_store, harness, goal_id=1, status="complete", outcome="failed")
    make_goal(coworld_store, harness, title="Other")
    insert_task(coworld_store, harness, goal_id=2, status="complete", outcome="succeeded")
    goal = goals.read(coworld_store, 1)
    assert goal["last_progress"] == progress_at
    assert goal["ideation_rounds_without_progress"] == 1

    clock.advance(60)
    claim_at = "2026-10-28T21:09:05.123456Z"
    insert_claim(coworld_store, harness, tags=["G-1"])
    goal = goals.read(coworld_store, 1)
    assert goal["last_progress"] == claim_at
    assert goal["ideation_rounds_without_progress"] == 0

    clock.advance(60)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=4,
    )  # fmt: skip
    clock.advance(60)
    version_at = "2026-10-28T21:11:05.123456Z"
    insert_version(coworld_store, harness, task_id=research)
    goal = goals.read(coworld_store, 1)
    assert goal["last_progress"] == version_at
    assert goal["ideation_rounds_without_progress"] == 0

    clock.advance(60)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=5,
    )  # fmt: skip
    clock.advance(60)
    experiment = insert_experiment(coworld_store, harness, goal_id=1, status="running")
    insert_version(coworld_store, harness, experiment_id=experiment)
    assert goals.read(coworld_store, 1)["ideation_rounds_without_progress"] == 0


def test_progress_since_round(coworld_store, harness, clock):
    make_goal(coworld_store, harness)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=1,
    )  # fmt: skip
    clock.advance(60)
    insert_task(coworld_store, harness, goal_id=1, status="complete", outcome="succeeded")
    clock.advance(60)
    insert_task(
        coworld_store, harness, goal_id=1, type="ideation", status="complete",
        outcome="succeeded", ideation_round=2,
    )  # fmt: skip
    assert goals.progress_since_round(coworld_store, 1, 1) is True
    assert goals.progress_since_round(coworld_store, 1, 2) is False
    with pytest.raises(NotFound):
        goals.progress_since_round(coworld_store, 1, 3)
    with pytest.raises(NotFound):
        goals.progress_since_round(coworld_store, 2, 1)


def test_read_includes_the_goals_active_directives(coworld_store, harness):
    make_goal(coworld_store, harness)
    make_goal(coworld_store, harness, title="Other")
    directives.add(coworld_store, harness, text="Keep the opening", scope="goal:1", source="m1")
    directives.add(coworld_store, harness, text="Old", scope="goal:1", source="m2")
    directives.add(coworld_store, harness, text="Elsewhere", scope="goal:2", source="m3")
    directives.retire(coworld_store, harness, 2)
    listed = goals.read(coworld_store, 1)["directives"]
    assert [(d["id"], d["text"], d["status"]) for d in listed] == [
        ("D-1", "Keep the opening", "active")
    ]


def test_read_of_none_is_the_workspace_maintenance_pseudo_goal(coworld_store, harness):
    make_goal(coworld_store, harness)
    insert_task(coworld_store, harness, goal_id=None, type="maintenance", status="unclaimed")
    insert_task(
        coworld_store, harness, goal_id=None, type="maintenance", status="complete",
        outcome="succeeded",
    )  # fmt: skip
    insert_task(coworld_store, harness, goal_id=1, type="maintenance", status="unclaimed")
    pseudo = goals.read(coworld_store, None)
    assert pseudo["id"] is None
    assert pseudo["title"] == goals.WORKSPACE_MAINTENANCE_TITLE == "workspace maintenance"
    assert pseudo["tasks"]["unclaimed"] == 1 and pseudo["tasks"]["complete"] == 1
    assert pseudo["tasks"]["open"] == ["T-1"]
    # The Goal's own read does not pick up the goal-less Tasks.
    assert goals.read(coworld_store, 1)["tasks"]["open"] == ["T-3"]
    assert goals.read(coworld_store, 1)["tasks"]["complete"] == 0


def test_read_of_a_missing_goal_raises_not_found(coworld_store):
    with pytest.raises(NotFound) as info:
        goals.read(coworld_store, 3)
    assert info.value.problems == ["G-3 does not exist"]


# --- list and needs_ideation -------------------------------------------------------------------


def test_list_filters_by_status(coworld_store, harness):
    make_goal(coworld_store, harness, title="A")
    make_goal(coworld_store, harness, title="B")
    goals.set_status(coworld_store, harness, 2, "parked", "later")
    assert [g["id"] for g in goals.list(coworld_store)] == ["G-1", "G-2"]
    assert [g["title"] for g in goals.list(coworld_store, status="parked")] == ["B"]
    assert goals.list(coworld_store, status="completed") == []
    assert "tasks" not in goals.list(coworld_store)[0]
    with pytest.raises(StoreError):
        goals.list(coworld_store, status="bogus")


def test_needs_ideation(coworld_store, harness):
    make_goal(coworld_store, harness, title="no tasks")
    make_goal(coworld_store, harness, title="open task")
    insert_task(coworld_store, harness, goal_id=2, status="proposed")
    make_goal(coworld_store, harness, title="only finished tasks")
    insert_task(coworld_store, harness, goal_id=3, status="complete", outcome="succeeded")
    insert_task(coworld_store, harness, goal_id=3, status="abandoned", outcome="abandoned")
    make_goal(coworld_store, harness, title="parked")
    goals.set_status(coworld_store, harness, 4, "parked", "later")
    make_goal(coworld_store, harness, title="waiting on the user")
    insert_task(
        coworld_store, harness, goal_id=5, status="complete", outcome="succeeded",
        waiting_on={"kind": "user", "detail": "approve the plan"},
    )  # fmt: skip
    make_goal(coworld_store, harness, title="in progress")
    insert_task(coworld_store, harness, goal_id=6, status="in-progress")
    assert goals.needs_ideation(coworld_store) == [1, 3]
