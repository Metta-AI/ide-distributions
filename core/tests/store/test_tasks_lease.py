"""Leases with epochs: one claim wins, stale leases are taken over, old holders are refused.

Covers the review focus "two concurrent task.claim calls, or a claim from a replaced run"
(overview) and A2.3: every Task-scoped write is tried from a stale epoch.
"""

from __future__ import annotations

import pytest

from research_core.store import tasks
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import LeaseError
from tests.store.test_tasks import SHA, claim, make_goal, make_task, make_version

FIRST = Actor.session("api_1761690300_first")
SECOND = Actor.session("api_1761690400_second")


@pytest.fixture
def goal(coworld_store, harness) -> int:
    return make_goal(coworld_store, harness)


@pytest.fixture
def held(coworld_store, harness, goal) -> dict:
    """A Task held by FIRST at epoch 1."""
    task = make_task(coworld_store, harness, goal)
    return claim(coworld_store, FIRST, task["id"], n=1)


def test_second_claim_gets_lease_error_while_the_lease_is_fresh(coworld_store, held, clock):
    other_connection = Store.open(coworld_store.path, kind="coworld", clock=clock)
    try:
        with pytest.raises(LeaseError) as info:
            claim(other_connection, SECOND, held["id"], n=2)
        assert info.value.problems == [
            f"{held['id']} is held by session:{FIRST.id} with a fresh lease"
        ]
    finally:
        other_connection.close()
    after = tasks.read(coworld_store, held["id"])
    assert after["held_by"] == FIRST.id
    assert after["lease_epoch"] == 1
    assert [s["session_id"] for s in after["sessions"]] == [FIRST.id]


def test_stale_lease_is_taken_over_with_a_higher_epoch(coworld_store, held, clock):
    clock.advance(299)
    with pytest.raises(LeaseError):
        claim(coworld_store, SECOND, held["id"], n=2)
    clock.advance(2)
    taken = claim(coworld_store, SECOND, held["id"], n=2)
    assert taken["held_by"] == SECOND.id
    assert taken["lease_epoch"] == 2
    assert taken["run_id"] == "run-2"
    assert taken["worktree"] == "/w/task-2"
    assert {(s["session_id"], s["epoch"]) for s in taken["sessions"]} == {
        (FIRST.id, 1),
        (SECOND.id, 2),
    }
    event = coworld_store.events_after(0)[-1]
    assert event.kind == "task.claimed"
    assert event.payload["epoch"] == 2
    assert event.payload["took_over_from"] == FIRST.id

    with pytest.raises(LeaseError) as info:
        tasks.require_holder(coworld_store, held["id"], FIRST)
    assert FIRST.id in info.value.problems[0]
    assert tasks.require_holder(coworld_store, held["id"], SECOND) is None


def test_same_holder_reclaim_renews_without_raising_the_epoch(coworld_store, held, clock):
    clock.advance(400)
    again = claim(coworld_store, FIRST, held["id"], n=3)
    assert again["lease_epoch"] == 1
    assert again["held_by"] == FIRST.id
    assert again["run_id"] == "run-3"
    assert again["lease_renewed"] == coworld_store.now()
    assert len(again["sessions"]) == 1
    with pytest.raises(LeaseError):
        claim(coworld_store, SECOND, held["id"], n=2)


def test_renew_lease_keeps_it_fresh(coworld_store, held, clock):
    clock.advance(200)
    renewed = tasks.renew_lease(coworld_store, FIRST, held["id"])
    assert renewed["lease_renewed"] == coworld_store.now()
    clock.advance(200)
    with pytest.raises(LeaseError):
        claim(coworld_store, SECOND, held["id"], n=2)
    with pytest.raises(LeaseError):
        tasks.renew_lease(coworld_store, SECOND, held["id"])


def test_require_holder_cases(coworld_store, harness, goal, held):
    unheld = make_task(coworld_store, harness, goal, title="unheld")
    with pytest.raises(LeaseError, match="not held"):
        tasks.require_holder(coworld_store, unheld["id"], FIRST)
    with pytest.raises(LeaseError, match="not a session"):
        tasks.require_holder(coworld_store, held["id"], harness)
    with pytest.raises(LeaseError, match="does not hold"):
        tasks.require_holder(coworld_store, held["id"], SECOND)
    # Actor.epoch is a test override of the session row's epoch.
    with pytest.raises(LeaseError, match="epoch"):
        tasks.require_holder(coworld_store, held["id"], Actor.session(FIRST.id, epoch=0))
    assert tasks.require_holder(coworld_store, held["id"], Actor.session(FIRST.id, epoch=1)) is None


def _complete(store, actor, task_id):
    return tasks.complete(
        store,
        actor,
        task_id,
        checklist={"claims": "none: nothing"},
        commit=SHA,
        merge_request="yes",
        result="done",
    )


def _propose_submission(store, actor, task_id):
    version = store.one("SELECT id FROM versions ORDER BY id DESC LIMIT 1")[0]
    return tasks.propose_submission(store, actor, task_id, version_id=version, reason="r")


HOLDER_WRITES = {
    "renew_lease": tasks.renew_lease,
    "wait": lambda s, a, t: tasks.wait(s, a, t, kind="xp", detail="runs"),
    "ask": lambda s, a, t: tasks.ask(s, a, t, question="which league?"),
    "release": lambda s, a, t: tasks.release(s, a, t, reason="handing back"),
    "complete": _complete,
    "fail": lambda s, a, t: tasks.fail(s, a, t, failure={"what_failed": "x", "next_step": "y"}),
    "propose_submission": _propose_submission,
    "set_last_error": lambda s, a, t: tasks.set_last_error(s, a, t, {"command": "x"}),
    "message": lambda s, a, t: tasks.message(s, a, t, text="hi", direction="to_orchestrator"),
}


@pytest.mark.parametrize("name", sorted(HOLDER_WRITES))
def test_every_holder_write_refuses_a_stale_epoch(coworld_store, harness, held, clock, name):
    make_version(coworld_store, harness)
    write = HOLDER_WRITES[name]
    clock.advance(301)
    claim(coworld_store, SECOND, held["id"], n=2)
    with pytest.raises(LeaseError):
        write(coworld_store, FIRST, held["id"])
    with pytest.raises(LeaseError):
        write(coworld_store, harness, held["id"])
    # The current holder at the current epoch may write.
    result = write(coworld_store, SECOND, held["id"])
    assert result["id"] == held["id"]
    assert result["lease_epoch"] == 2
    assert tasks.read(coworld_store, held["id"])["held_by"] in (SECOND.id, None)


def test_stale_holder_cannot_complete_after_takeover_and_the_new_holder_can(
    coworld_store, held, clock
):
    """The merge uses the commit the current holder recorded, never the replaced run's."""
    clock.advance(301)
    claim(coworld_store, SECOND, held["id"], n=2)
    with pytest.raises(LeaseError):
        _complete(coworld_store, FIRST, held["id"])
    assert tasks.read(coworld_store, held["id"])["completed_commit"] is None
    done = _complete(coworld_store, SECOND, held["id"])
    assert done["completed_commit"] == SHA
    assert done["held_by"] == SECOND.id
