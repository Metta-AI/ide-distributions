"""Intents (B.13): one row per external action, unique by (kind, key), resolved once."""

from __future__ import annotations

import pytest

from research_core.store import intents
from research_core.store.errors import NotFound, StoreError

T0 = "2026-10-28T21:04:05.123456Z"
T1 = "2026-10-28T21:05:05.123456Z"


def events(store):
    return [(e.kind, e.subject, e.payload) for e in store.events_after(0)]


def test_begin_creates_a_pending_intent_with_an_event(coworld_store, session_actor):
    record = intents.begin(
        coworld_store, session_actor, kind="upload", key="sha256:abc", subject="V-7"
    )
    assert record == {
        "id": "I-1",
        "kind": "upload",
        "subject": "V-7",
        "key": "sha256:abc",
        "state": "pending",
        "result": None,
        "created": T0,
        "resolved": None,
        "created_by": "session:api_1761690245_0badcafe",
        "attempt": 1,
        "existing": False,
        "retried": False,
    }
    assert events(coworld_store) == [
        (
            "intent.pending",
            "I-1",
            {"kind": "upload", "key": "sha256:abc", "subject": "V-7", "attempt": 1},
        )
    ]
    assert coworld_store.unmarked_changes() == []


def test_begin_returns_the_existing_pending_row(coworld_store, session_actor, clock):
    first = intents.begin(coworld_store, session_actor, kind="upload", key="k", subject="V-7")
    clock.advance(60)
    second = intents.begin(coworld_store, session_actor, kind="upload", key="k", subject="V-7")
    assert second["id"] == first["id"] == "I-1"
    assert second["existing"] is True
    assert second["retried"] is False
    assert second["state"] == "pending"
    assert second["created"] == T0
    assert len(events(coworld_store)) == 1


def test_begin_returns_a_done_row_for_the_caller_to_decide(coworld_store, session_actor):
    intents.begin(coworld_store, session_actor, kind="submit", key="V-7:league_1", subject="V-7")
    intents.finish(coworld_store, session_actor, 1, state="done", result={"submission": "sub_1"})
    again = intents.begin(
        coworld_store, session_actor, kind="submit", key="V-7:league_1", subject="V-7"
    )
    assert again["existing"] is True
    assert again["state"] == "done"
    assert again["result"] == {"submission": "sub_1"}
    assert [kind for kind, _, _ in events(coworld_store)] == ["intent.pending", "intent.done"]


def test_the_same_key_in_another_kind_is_a_new_intent(coworld_store, session_actor):
    intents.begin(coworld_store, session_actor, kind="upload", key="k", subject=None)
    other = intents.begin(coworld_store, session_actor, kind="submit", key="k", subject=None)
    assert other["id"] == "I-2"
    assert other["existing"] is False


def test_finish_records_the_result_and_the_event(coworld_store, session_actor, clock):
    intents.begin(coworld_store, session_actor, kind="xp", key="X-5:1", subject="X-5")
    clock.advance(60)
    record = intents.finish(
        coworld_store, session_actor, 1, state="done", result={"request_id": "xreq_1"}
    )
    assert record["state"] == "done"
    assert record["result"] == {"request_id": "xreq_1"}
    assert record["resolved"] == T1
    assert record["created"] == T0
    assert record["attempt"] == 1
    assert events(coworld_store)[1] == (
        "intent.done",
        "I-1",
        {"kind": "xp", "key": "X-5:1", "subject": "X-5", "result": {"request_id": "xreq_1"}},
    )


def test_finish_failed_emits_intent_failed(coworld_store, session_actor):
    intents.begin(coworld_store, session_actor, kind="forum", key="h", subject=None)
    record = intents.finish(coworld_store, session_actor, 1, state="failed", result={"status": 502})
    assert record["state"] == "failed"
    assert events(coworld_store)[1][0] == "intent.failed"


def test_finish_refuses_an_unknown_or_resolved_intent(coworld_store, session_actor):
    with pytest.raises(NotFound):
        intents.finish(coworld_store, session_actor, 99, state="done", result={})
    intents.begin(coworld_store, session_actor, kind="run", key="s:1", subject=None)
    intents.finish(coworld_store, session_actor, 1, state="done", result={"run_id": "r1"})
    with pytest.raises(StoreError) as info:
        intents.finish(coworld_store, session_actor, 1, state="failed", result={})
    assert info.value.problems == ["I-1 is already done"]
    assert intents.read(coworld_store, 1)["result"] == {"run_id": "r1"}


def test_finish_validates_state_and_result_at_once(coworld_store, session_actor):
    intents.begin(coworld_store, session_actor, kind="run", key="s:1", subject=None)
    with pytest.raises(StoreError) as info:
        intents.finish(coworld_store, session_actor, 1, state="maybe", result="nope")
    assert len(info.value.problems) == 2


def test_a_failed_intent_is_retried_with_its_failures_kept(coworld_store, session_actor, clock):
    intents.begin(coworld_store, session_actor, kind="upload", key="k", subject="V-7")
    intents.finish(coworld_store, session_actor, 1, state="failed", result={"status": 502})
    clock.advance(60)
    retry = intents.begin(coworld_store, session_actor, kind="upload", key="k", subject="V-7")
    assert retry["id"] == "I-1"
    assert retry["state"] == "pending"
    assert retry["existing"] is True
    assert retry["retried"] is True
    assert retry["attempt"] == 2
    assert retry["result"] == {"failures": [{"status": 502}]}
    assert retry["resolved"] is None
    assert retry["created"] == T0
    kinds = [kind for kind, _, _ in events(coworld_store)]
    assert kinds == ["intent.pending", "intent.failed", "intent.pending"]
    assert events(coworld_store)[2][2]["attempt"] == 2

    intents.finish(coworld_store, session_actor, 1, state="failed", result={"status": 503})
    third = intents.begin(coworld_store, session_actor, kind="upload", key="k", subject="V-7")
    assert third["attempt"] == 3
    assert third["result"] == {"failures": [{"status": 502}, {"status": 503}]}
    done = intents.finish(coworld_store, session_actor, 1, state="done", result={"pv": "pv_1"})
    assert done["result"] == {"pv": "pv_1", "failures": [{"status": 502}, {"status": 503}]}
    assert done["attempt"] == 3


def test_pending_lists_by_kind_in_order(coworld_store, session_actor):
    intents.begin(coworld_store, session_actor, kind="upload", key="a", subject=None)
    intents.begin(coworld_store, session_actor, kind="run", key="b", subject=None)
    intents.begin(coworld_store, session_actor, kind="upload", key="c", subject=None)
    intents.finish(coworld_store, session_actor, 1, state="done", result={})
    assert [i["id"] for i in intents.pending(coworld_store)] == ["I-2", "I-3"]
    assert [i["id"] for i in intents.pending(coworld_store, kind="upload")] == ["I-3"]
    assert intents.pending(coworld_store, kind="wiki") == []
    with pytest.raises(StoreError):
        intents.pending(coworld_store, kind="bogus")


def test_begin_validates_kind_key_and_subject(coworld_store, session_actor):
    with pytest.raises(StoreError) as info:
        intents.begin(coworld_store, session_actor, kind="email", key="", subject=7)
    assert len(info.value.problems) == 3
    assert intents.pending(coworld_store) == []
