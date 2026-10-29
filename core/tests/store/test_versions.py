"""Versions: built from a commit or adopted from a platform upload; uploaded at most once (B.6)."""

from __future__ import annotations

import sys
import types

import pytest

import research_core.store as store_package
from research_core.store import versions
from research_core.store.actors import Actor
from research_core.store.errors import LeaseError, NotFound, StoreError

T0 = "2026-10-28T21:04:05.123456Z"
PVID = "7b1c0f4e-2f1a-4c0d-9c3e-1a2b3c4d5e6f"
OTHER_PVID = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
ARTIFACT = {"kind": "file", "path": "dist/policy.bin", "digest": "sha256:" + "0" * 64, "size": 2048}


def make(store, actor, n=1, **overrides):
    fields = {
        "summary": f"policy {n}",
        "commit_sha": f"{n:040x}",
        "branch": "main",
        "artifact": ARTIFACT,
        "based_on": [],
    }
    fields.update(overrides)
    return versions.create(store, actor, **fields)


def event_kinds(store):
    return [e.kind for e in store.events_after(0)]


def insert_task(store, actor) -> None:
    """A goal and an in-progress Task written directly: the tasks module is not needed here."""
    with store.tx(actor) as tx:
        tx.execute(
            "INSERT INTO goals(title, end_state, reason, status, created, created_by, updated)"
            " VALUES ('g', 'e', 'r', 'active', ?, 'harness', ?)",
            (tx.at, tx.at),
        )
        tx.execute(
            "INSERT INTO tasks(goal_id, type, title, description, status, created, created_by,"
            " updated) VALUES (1, 'improvement', 't', 'd', 'in-progress', ?, 'harness', ?)",
            (tx.at, tx.at),
        )
        tx.event("task.created", "T-1", {})


def test_create_built_version_with_parents(coworld_store, session_actor):
    record = make(coworld_store, session_actor, summary="Starter policy", commit_sha="0" * 40)
    assert record == {
        "id": "V-1",
        "summary": "Starter policy",
        "source": "built",
        "commit_sha": "0" * 40,
        "branch": "main",
        "artifact": ARTIFACT,
        "task_id": None,
        "experiment_id": None,
        "uploaded": None,
        "image": None,
        "policy_name": None,
        "created": T0,
        "created_by": "session:api_1761690245_0badcafe",
        "based_on": [],
        "experiments": [],
        "submission_status": "built",
    }
    child = versions.create(
        coworld_store,
        session_actor,
        summary="Retreat at 30% health",
        commit_sha="ABCDEF1",
        branch="task/1",
        artifact={**ARTIFACT, "digest": "sha256:" + "1" * 64},
        based_on=["V-1"],
        image="sha256:" + "f" * 64,
        policy_name="games-bond",
    )
    assert child["id"] == "V-2"
    assert child["commit_sha"] == "abcdef1"
    assert child["based_on"] == ["V-1"]
    assert child["image"] == "sha256:" + "f" * 64 and child["policy_name"] == "games-bond"
    rows = coworld_store.q("SELECT version_id, parent_id FROM version_parents")
    assert [tuple(row) for row in rows] == [(2, 1)]
    events = coworld_store.events_after(0)
    assert [e.kind for e in events] == ["version.created", "version.created"]
    assert events[-1].subject == "V-2"
    assert events[-1].payload == {"source": "built", "commit_sha": "abcdef1", "based_on": ["V-1"]}
    assert versions.read(coworld_store, "V-2") == child
    assert coworld_store.unmarked_changes() == []


def test_create_reports_every_problem_at_once(coworld_store, session_actor):
    with pytest.raises(StoreError) as exc_info:
        versions.create(
            coworld_store,
            session_actor,
            summary=" ",
            commit_sha="xyz",
            branch="",
            artifact={"kind": "image", "path": "", "size": -1, "extra": 1},
            based_on=["V-9", "G-1", "V-9"],
            task_id=4,
            experiment_id="X-2",
        )
    assert exc_info.value.problems == [
        "summary is required",
        "commit_sha must be 7 to 40 hex digits",
        "branch is required",
        "artifact.kind is 'image'; only file artifacts are supported (image policies are not)",
        "artifact.path is required",
        "artifact.digest is required",
        "artifact.size must be a whole number of bytes",
        "artifact.extra is not an artifact field (kind, path, digest, size)",
        "based_on: V-9 does not exist",
        "based_on: 'G-1' is not a Version reference (V-<n>)",
        "based_on: V-9 is listed twice",
        "task_id: T-4 does not exist",
        "experiment_id: X-2 does not exist",
    ]
    assert coworld_store.q("SELECT * FROM versions") == []
    with pytest.raises(StoreError) as exc_info:
        versions.create(
            coworld_store,
            session_actor,
            summary="x",
            commit_sha="0" * 40,
            branch="main",
            artifact="dist/policy.bin",
            based_on="V-1",
            image=7,
            policy_name="",
        )
    assert exc_info.value.problems == [
        "artifact must be an object {kind, path, digest, size}",
        "based_on must be a list of Version references",
        "image must be text",
        "policy_name must be text",
    ]


def test_adopt_records_an_uploaded_version_without_a_commit(coworld_store, session_actor):
    record = versions.adopt(
        coworld_store,
        session_actor,
        summary="Uploaded from the desktop app",
        policy_version_id=PVID,
        policy_name="games-bond",
        uploaded_at="2026-10-27T18:00:00Z",
        digest="sha256:" + "a" * 64,
    )
    assert record["id"] == "V-1"
    assert record["source"] == "adopted"
    assert record["commit_sha"] is None and record["branch"] is None
    assert record["artifact"] is None
    assert record["uploaded"] == {
        "policy_version_id": PVID,
        "at": "2026-10-27T18:00:00Z",
        "digest": "sha256:" + "a" * 64,
    }
    assert record["policy_name"] == "games-bond"
    assert record["submission_status"] == "uploaded"
    assert record["based_on"] == []
    event = coworld_store.events_after(0)[-1]
    assert event.kind == "version.created" and event.subject == "V-1"
    assert event.payload == {"source": "adopted", "policy_version_id": PVID}
    without_digest = versions.adopt(
        coworld_store,
        session_actor,
        summary="Another upload",
        policy_version_id=OTHER_PVID,
        policy_name="games-bond",
        uploaded_at="2026-10-27T19:00:00Z",
    )
    assert without_digest["uploaded"] == {
        "policy_version_id": OTHER_PVID,
        "at": "2026-10-27T19:00:00Z",
    }
    with pytest.raises(StoreError) as exc_info:
        versions.adopt(
            coworld_store,
            session_actor,
            summary="Twice",
            policy_version_id=PVID,
            policy_name="games-bond",
            uploaded_at="2026-10-27T18:00:00Z",
        )
    assert exc_info.value.problems == [f"policy version {PVID} is already V-1"]
    with pytest.raises(StoreError) as exc_info:
        versions.adopt(
            coworld_store,
            session_actor,
            summary="",
            policy_version_id="",
            policy_name="",
            uploaded_at="yesterday",
            digest=" ",
        )
    assert exc_info.value.problems == [
        "summary is required",
        "policy_version_id is required",
        "policy_name is required",
        "uploaded_at must be an ISO 8601 time",
        "digest must be text",
    ]


def test_mark_uploaded_happens_at_most_once(coworld_store, session_actor, clock):
    make(coworld_store, session_actor)
    make(coworld_store, session_actor, 2)
    clock.advance(60)
    record = versions.mark_uploaded(
        coworld_store,
        session_actor,
        "V-1",
        policy_version_id=PVID,
        at="2026-10-28T21:05:05Z",
        digest_checked=True,
    )
    assert record["uploaded"] == {
        "policy_version_id": PVID,
        "at": "2026-10-28T21:05:05Z",
        "digest_checked": True,
    }
    assert record["submission_status"] == "uploaded"
    event = coworld_store.events_after(0)[-1]
    assert event.kind == "version.uploaded" and event.subject == "V-1"
    assert event.payload == record["uploaded"]
    seq = coworld_store.last_seq()
    # The same policy version again is a no-op: no event, nothing changes.
    again = versions.mark_uploaded(
        coworld_store,
        session_actor,
        1,
        policy_version_id=PVID,
        at="2026-10-28T23:00:00Z",
        digest_checked=False,
    )
    assert again == record and coworld_store.last_seq() == seq
    with pytest.raises(StoreError) as exc_info:
        versions.mark_uploaded(
            coworld_store,
            session_actor,
            1,
            policy_version_id=OTHER_PVID,
            at="2026-10-28T23:00:00Z",
            digest_checked=True,
        )
    assert exc_info.value.problems == [
        f"V-1 is already uploaded as policy version {PVID}; a Version is uploaded at most once"
    ]
    with pytest.raises(StoreError) as exc_info:
        versions.mark_uploaded(
            coworld_store,
            session_actor,
            2,
            policy_version_id=PVID,
            at="2026-10-28T23:00:00Z",
            digest_checked=True,
        )
    assert exc_info.value.problems == [f"policy version {PVID} already belongs to V-1"]
    with pytest.raises(StoreError) as exc_info:
        versions.mark_uploaded(
            coworld_store, session_actor, 2, policy_version_id="", at="soon", digest_checked="yes"
        )
    assert exc_info.value.problems == [
        "policy_version_id is required",
        "at must be an ISO 8601 time",
        "digest_checked must be true or false",
    ]
    assert versions.read(coworld_store, 2)["uploaded"] is None
    assert coworld_store.last_seq() == seq
    with pytest.raises(NotFound):
        versions.mark_uploaded(
            coworld_store, session_actor, 9, policy_version_id=PVID, at=T0, digest_checked=True
        )


def test_find_by_policy_version(coworld_store, session_actor):
    make(coworld_store, session_actor)
    assert versions.find_by_policy_version(coworld_store, PVID) is None
    versions.mark_uploaded(
        coworld_store, session_actor, 1, policy_version_id=PVID, at=T0, digest_checked=True
    )
    assert versions.find_by_policy_version(coworld_store, PVID)["id"] == "V-1"
    versions.adopt(
        coworld_store,
        session_actor,
        summary="adopted",
        policy_version_id=OTHER_PVID,
        policy_name="p",
        uploaded_at=T0,
    )
    assert versions.find_by_policy_version(coworld_store, OTHER_PVID)["id"] == "V-2"


def test_read_lists_experiments_that_use_the_version(coworld_store, session_actor):
    for n in (1, 2, 3):
        make(coworld_store, session_actor, n)
    # Two Experiments written directly (the experiments module is not needed here): one
    # compares V-1 with V-2, the other has V-2 as its baseline and no candidate yet.
    with coworld_store.tx(session_actor) as tx:
        for summary, methods in (
            ("V-1 against V-2", '{"baseline":"V-1","candidate":"V-2"}'),
            ("V-2 against nothing yet", '{"baseline":"V-2","candidate":null}'),
        ):
            cursor = tx.execute(
                "INSERT INTO experiments(summary, status, methods, created, created_by)"
                " VALUES (?, 'proposed', ?, ?, ?)",
                (summary, methods, tx.at, str(session_actor)),
            )
            tx.event("experiment.created", f"X-{cursor.lastrowid}", {})
    assert versions.read(coworld_store, 1)["experiments"] == [
        {"id": "X-1", "role": "baseline", "status": "proposed"}
    ]
    assert versions.read(coworld_store, "V-2")["experiments"] == [
        {"id": "X-1", "role": "candidate", "status": "proposed"},
        {"id": "X-2", "role": "baseline", "status": "proposed"},
    ]
    assert versions.read(coworld_store, 3)["experiments"] == []
    with pytest.raises(NotFound):
        versions.read(coworld_store, 9)
    with pytest.raises(StoreError) as exc_info:
        versions.read(coworld_store, "X-1")
    assert exc_info.value.problems == ["'X-1' is not a Version reference (V-<n>)"]


def test_list_is_newest_first_with_a_limit(coworld_store, session_actor):
    for n in (1, 2, 3):
        make(coworld_store, session_actor, n)
    listed = versions.list(coworld_store)
    assert [v["id"] for v in listed] == ["V-3", "V-2", "V-1"]
    assert listed[0] == versions.read(coworld_store, 3)
    assert [v["id"] for v in versions.list(coworld_store, limit=2)] == ["V-3", "V-2"]
    assert versions.list(coworld_store, limit=0) == []


def test_create_with_a_task_requires_its_holder(coworld_store, session_actor, harness, monkeypatch):
    insert_task(coworld_store, harness)
    calls: list = []
    fake = types.ModuleType("research_core.store.tasks")

    def require_holder(store, task_id, actor):
        calls.append((task_id, actor))
        if actor.epoch == 1:
            raise LeaseError([f"T-{task_id} is held at a newer epoch than this Session's"])

    fake.require_holder = require_holder
    monkeypatch.setitem(sys.modules, "research_core.store.tasks", fake)
    monkeypatch.setattr(store_package, "tasks", fake, raising=False)
    stale = Actor.session(session_actor.id, epoch=1)
    with pytest.raises(LeaseError):
        make(coworld_store, stale, task_id="T-1")
    assert coworld_store.q("SELECT * FROM versions") == []
    assert "version.created" not in event_kinds(coworld_store)
    record = make(coworld_store, session_actor, task_id=1)
    assert record["task_id"] == 1
    assert calls == [(1, stale), (1, session_actor)]
    # Without a Task there is no holder to check.
    make(coworld_store, stale, 2)
    assert len(calls) == 2


def test_submission_status_reads_the_record():
    assert versions.submission_status({"uploaded": None}) == "built"
    assert versions.submission_status({"uploaded": {"policy_version_id": PVID}}) == "uploaded"
