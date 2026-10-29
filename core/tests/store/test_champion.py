"""The champion record: one row per league, history and an event only on a real change (B.6)."""

from __future__ import annotations

import pytest

from research_core.store import champion, versions
from research_core.store.errors import StoreError

T0 = "2026-10-28T21:04:05.123456Z"
PVID = "7b1c0f4e-2f1a-4c0d-9c3e-1a2b3c4d5e6f"
OTHER_PVID = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
ARTIFACT = {"kind": "file", "path": "dist/policy.bin", "digest": "sha256:" + "0" * 64, "size": 2048}


def sync(store, actor, **overrides):
    fields = {
        "league_id": "league_pudge",
        "division_id": "div_bronze",
        "membership_id": "lpm_1",
        "policy_version_id": PVID,
        "state": "pending",
    }
    fields.update(overrides)
    return champion.sync(store, actor, **fields)


def changed_events(store):
    return [e for e in store.events_after(0) if e.kind == "champion.changed"]


def test_first_sync_inserts_the_row_with_history_and_an_event(coworld_store, harness):
    assert champion.read(coworld_store, "league_pudge") is None
    record = sync(coworld_store, harness)
    assert record == {
        "league_id": "league_pudge",
        "division_id": "div_bronze",
        "membership_id": "lpm_1",
        "version_id": None,
        "policy_version_id": PVID,
        "state": "pending",
        "since": T0,
        "approved_by": None,
        "reason": None,
        "synced": T0,
        "history": [{"policy_version_id": PVID, "state": "pending", "at": T0, "note": None}],
    }
    assert champion.read(coworld_store, "league_pudge") == record
    events = changed_events(coworld_store)
    assert len(events) == 1
    assert events[0].subject == "league_pudge" and events[0].actor == "harness"
    assert events[0].payload == {
        "league_id": "league_pudge",
        "policy_version_id": PVID,
        "state": "pending",
        "version_id": None,
        "previous": None,
    }
    assert coworld_store.unmarked_changes() == []


def test_unchanged_sync_refreshes_synced_without_history_or_event(coworld_store, harness, clock):
    sync(coworld_store, harness, reason="first submission")
    seq = coworld_store.last_seq()
    clock.advance(600)
    record = sync(coworld_store, harness, division_id="div_silver")
    assert record["synced"] == "2026-10-28T21:14:05.123456Z"
    assert record["since"] == T0
    assert record["division_id"] == "div_silver"
    assert record["reason"] == "first submission"
    assert len(record["history"]) == 1
    assert coworld_store.last_seq() == seq
    assert coworld_store.q("SELECT * FROM champion_history") != []
    assert len(coworld_store.q("SELECT * FROM champion")) == 1


def test_state_and_policy_changes_are_recorded_newest_first(coworld_store, harness, clock):
    sync(coworld_store, harness)
    clock.advance(60)
    sync(coworld_store, harness, state="placed", reason="placed in bronze")
    clock.advance(60)
    sync(coworld_store, harness, state="champion")
    clock.advance(60)
    record = sync(
        coworld_store, harness, policy_version_id=OTHER_PVID, state="pending", reason="V-2 sent"
    )
    assert record["state"] == "pending"
    assert record["policy_version_id"] == OTHER_PVID
    assert record["since"] == "2026-10-28T21:07:05.123456Z"
    assert record["reason"] == "V-2 sent"
    history = [(h["policy_version_id"], h["state"], h["at"], h["note"]) for h in record["history"]]
    assert history == [
        (OTHER_PVID, "pending", "2026-10-28T21:07:05.123456Z", "V-2 sent"),
        (PVID, "champion", "2026-10-28T21:06:05.123456Z", None),
        (PVID, "placed", "2026-10-28T21:05:05.123456Z", "placed in bronze"),
        (PVID, "pending", T0, None),
    ]
    events = changed_events(coworld_store)
    assert len(events) == 4
    assert events[1].payload["previous"] == {"policy_version_id": PVID, "state": "pending"}
    assert events[-1].payload["previous"] == {"policy_version_id": PVID, "state": "champion"}
    # A reason given once is kept by later syncs that give none.
    clock.advance(60)
    assert sync(coworld_store, harness, policy_version_id=OTHER_PVID)["reason"] == "V-2 sent"


def test_sync_links_the_local_version_by_policy_version(coworld_store, session_actor, harness):
    versions.create(
        coworld_store,
        session_actor,
        summary="starter",
        commit_sha="0" * 40,
        branch="main",
        artifact=ARTIFACT,
        based_on=[],
    )
    versions.mark_uploaded(
        coworld_store, session_actor, 1, policy_version_id=PVID, at=T0, digest_checked=True
    )
    record = sync(coworld_store, harness)
    assert record["version_id"] == 1
    assert changed_events(coworld_store)[-1].payload["version_id"] == 1
    # A policy version uploaded outside the workspace has no local Version until adopted.
    other = sync(coworld_store, harness, league_id="league_other", policy_version_id=OTHER_PVID)
    assert other["version_id"] is None
    assert len(coworld_store.q("SELECT * FROM champion")) == 2


def test_an_empty_league_has_state_none_and_no_policy_version(coworld_store, harness):
    record = sync(
        coworld_store,
        harness,
        division_id=None,
        membership_id=None,
        policy_version_id=None,
        state="none",
    )
    assert record["state"] == "none" and record["policy_version_id"] is None
    assert record["history"] == [
        {"policy_version_id": None, "state": "none", "at": T0, "note": None}
    ]


def test_sync_validates_its_inputs(coworld_store, harness):
    with pytest.raises(StoreError) as exc_info:
        champion.sync(
            coworld_store,
            harness,
            league_id="",
            division_id=3,
            membership_id=4,
            policy_version_id=None,
            state="winner",
            reason=5,
        )
    assert exc_info.value.problems == [
        "league_id is required",
        "division_id must be text",
        "membership_id must be text",
        "state must be pending, processing, placed, rejected, champion or none",
        "policy_version_id is required unless state is none",
        "reason must be text",
    ]
    with pytest.raises(StoreError) as exc_info:
        sync(coworld_store, harness, policy_version_id=None)
    assert exc_info.value.problems == ["policy_version_id is required unless state is none"]
    with pytest.raises(StoreError) as exc_info:
        sync(coworld_store, harness, policy_version_id=7)
    assert exc_info.value.problems == ["policy_version_id must be text"]
    assert champion.read(coworld_store, "league_pudge") is None
    assert coworld_store.events_after(0) == []
