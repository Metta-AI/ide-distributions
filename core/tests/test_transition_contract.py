"""Refinement tests: real SQLite transitions with lost replies and crash-cut transactions."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from research_core.coordinator import Coordinator
from research_core.store import admissions, db, sessions, tasks
from research_core.store.actors import Actor
from research_core.store.errors import LeaseError, StoreError
from research_core.watcher.hermes_api import FakeHermesRuns
from tests.test_coordinator_transitions import assigned
from tests.watcher._support import coordinator, dispatch_inputs, make_ctx, set_setting


class Crash(BaseException):
    pass


def test_late_acceptance_receipt_cannot_revive_completed_attempt(tmp_path):
    ctx = make_ctx(tmp_path)
    sessions.register(
        ctx.wstore, Actor.harness(), session_id="chat", profile="orchestrator", kind="chat"
    )
    request = dict(profile="orchestrator", session_id="chat", text="work", idempotency_key="stable")

    class CompletesBeforeReply(FakeHermesRuns):
        def start(self, **kwargs):
            run_id = super().start(**kwargs)
            self.complete(run_id)
            with db.Store.open(ctx.ws.store_path, kind="workspace") as store:
                row = store.one("SELECT * FROM client_admissions")
                owner.settle_attempt(
                    store, row["op_id"], self.status(run_id, profile="orchestrator")
                )
            return run_id

    native = CompletesBeforeReply()
    owner = Coordinator(ctx.ws, native)
    run_id = owner.start(**request)
    row = ctx.wstore.one("SELECT * FROM client_admissions")
    assert row["status"] == "completed" and row["run_id"] == run_id
    assert owner.start(**request) == run_id
    with pytest.raises(sqlite3.IntegrityError, match="terminal execution"):
        with ctx.wstore.tx(Actor.harness()) as tx:
            tx.execute("UPDATE client_admissions SET status='running'")


@pytest.mark.parametrize("cut", ["before_commit", "after_commit"])
def test_retry_transfer_replays_exact_successor_and_fences_old_worker(tmp_path, monkeypatch, cut):
    ctx = make_ctx(tmp_path)
    predecessor = assigned(ctx)
    old_task = tasks.read(ctx.store, 1)
    ctx.hermes.fail(predecessor["run_id"], "crash")
    owner = coordinator(ctx)
    owner.reconcile()
    owner.advance_attempts(ctx)
    successor = ctx.wstore.one("SELECT * FROM client_admissions WHERE origin='continuation'")
    assert successor["session_id"] != predecessor["session_id"]
    saved = ctx.wstore.one("SELECT * FROM client_admissions WHERE op_id=?", (predecessor["op_id"],))
    assert (
        saved["successor_id"] == successor["op_id"] and saved["disposition"] == "successor queued"
    )
    original = db.Tx.__exit__

    def commit(tx, *args):
        if tx.store.kind == "coworld" and tx.store.one(
            "SELECT 1 FROM events WHERE kind='task.attempt_transferred' AND seq>?",
            (0,),
        ):
            if cut == "before_commit":
                original(tx, Crash, Crash(), None)
            else:
                original(tx, *args)
            raise Crash()
        return original(tx, *args)

    payload = json.loads(successor["payload"])
    request = dict(
        profile=successor["profile"],
        session_id=successor["session_id"],
        text=payload["input"],
        idempotency_key=successor["native_key"],
    )
    with monkeypatch.context() as patch:
        patch.setattr(db.Tx, "__exit__", commit)
        with pytest.raises(Crash):
            owner.start(**request)
    owner.start(**request)
    owner.advance_attempts(ctx)
    task = tasks.read(ctx.store, 1)
    assert task["held_by"] == successor["session_id"]
    assert task["lease_epoch"] == old_task["lease_epoch"] + 1
    assert (task["worktree"], task["branch"]) == (old_task["worktree"], old_task["branch"])
    with pytest.raises(LeaseError):
        tasks.wait(ctx.store, Actor.session(predecessor["session_id"]), 1, kind="xp", detail="late")
    assert len(ctx.hermes.runs) == 2


def test_successful_wait_cycles_do_not_spend_crash_budget(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    set_setting(ctx, "recovery.max_attempts", 1)
    tasks.wait(ctx.store, Actor.session(row["session_id"]), 1, kind="xp", detail="intentional")
    ctx.hermes.complete(row["run_id"])
    owner = coordinator(ctx)
    owner.reconcile()
    owner.advance_attempts(ctx)
    assert ctx.wstore.one("SELECT failure_counted FROM client_admissions")[0] == 0
    tasks.resolve_wait(ctx.store, Actor.harness(), 1, note="done")
    owner.advance_events(ctx)
    dispatch_inputs(ctx)
    assert tasks.read(ctx.store, 1)["status"] == "in-progress"
    assert len(ctx.hermes.runs) == 2


def test_unproductive_completed_runs_reach_failure_budget(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    set_setting(ctx, "recovery.max_attempts", 1)
    ctx.hermes.complete(row["run_id"], "I did nothing")
    owner = coordinator(ctx)
    owner.reconcile()
    owner.advance_attempts(ctx)
    assert tasks.read(ctx.store, 1)["outcome"] == "failed"
    assert ctx.wstore.one("SELECT failure_counted FROM client_admissions")[0] == 1


def test_wrong_receipt_and_uncertain_predecessor_cannot_authorize_progress(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    wrong = FakeHermesRuns()._status("unrelated")
    with pytest.raises(StoreError, match="another execution"):
        owner.settle_attempt(ctx.wstore, row["op_id"], wrong)
    del ctx.hermes.runs[row["run_id"]]
    owner.reconcile()
    owner.advance_attempts(ctx)
    [visible] = admissions.work_status(ctx.wstore)
    assert visible["state"] == "waiting" and visible["owner"].startswith("runtime:")
    assert visible["reason"] and visible["trigger"]
    assert ctx.wstore.one("SELECT count(*) FROM client_admissions")[0] == 1


def test_live_event_guidance_is_rerouted_when_parent_finishes_before_delivery(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    result = owner.continue_session(
        ctx,
        sessions.read(ctx.wstore, row["session_id"]),
        "Important wait-resolution guidance",
        key="event:wait-resolved:17",
        with_pending_messages=False,
    )
    ctx.hermes.complete(row["run_id"])
    dispatch_inputs(ctx)  # The event consumer has already acknowledged the originating event.
    for _ in range(3):
        owner.advance_attempts(ctx)
        dispatch_inputs(ctx)
    deliveries = [text for texts in ctx.hermes.steered.values() for text in texts]
    assert deliveries == ["Important wait-resolution guidance"]
    old = ctx.wstore.one("SELECT * FROM client_admissions WHERE native_key=?", (result["key"],))
    assert old["disposition"] == "delivery rerouted" and old["successor_id"]
    assert len(ctx.hermes.runs) == 2


def test_admitted_start_is_not_replayed_into_a_different_private_runtime(tmp_path):
    from research_core.watcher.hermes_api import RunsError, RunsUnavailable
    from tests.test_coordinator import LostResponse

    ctx = make_ctx(tmp_path)
    sessions.register(
        ctx.wstore, Actor.harness(), session_id="chat", profile="orchestrator", kind="chat"
    )
    native = LostResponse()
    request = dict(profile="orchestrator", session_id="chat", text="work", idempotency_key="same")
    with pytest.raises(RunsUnavailable):
        Coordinator(ctx.ws, native, "runtime-old").start(**request)
    with pytest.raises(RunsError, match="retired private runtime"):
        Coordinator(ctx.ws, native, "runtime-new").start(**request)
    assert len(native.calls) == 1
    [row] = ctx.wstore.q("SELECT * FROM client_admissions")
    assert row["status"] == "interrupted" and not row["execution_uncertain"]


def test_release_uses_committed_terminal_fact_when_native_receipt_was_retired(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    ctx.hermes.complete(row["run_id"])
    owner = coordinator(ctx)
    owner.reconcile()
    with ctx.wstore.tx(Actor.harness()) as tx:
        tx.execute("UPDATE task_assignments SET state='release_requested'")
    del ctx.hermes.runs[row["run_id"]]
    owner.advance_attempts(ctx)
    owner.finish_assignments(ctx.wstore)
    assert not ctx.wstore.q("SELECT * FROM task_assignments")
    assert tasks.read(ctx.store, 1)["status"] == "unclaimed"
    assert ctx.wstore.one("SELECT count(*) FROM client_admissions")[0] == 1


def test_native_stop_deadline_requests_exclusion_instead_of_wedging_release(tmp_path):
    ctx = make_ctx(tmp_path)
    assigned(ctx)
    owner = Coordinator(ctx.ws, ctx.hermes, "same-runtime")
    with ctx.wstore.tx(Actor.harness()) as tx:
        tx.execute("UPDATE client_admissions SET runtime_id='same-runtime'")
        tx.execute(
            "UPDATE task_assignments SET state='release_requested',"
            "recovery_deadline='2000-01-01T00:00:00Z'"
        )
    owner.finish_assignments(ctx.wstore)
    [uncertain] = ctx.wstore.q("SELECT * FROM client_admissions")
    assert uncertain["execution_uncertain"] and uncertain["status"] == "interrupted"
    assert ctx.wstore.one("SELECT count(*) FROM task_assignments")[0] == 1
    replacement = Coordinator(ctx.ws, ctx.hermes, "replacement-runtime")
    replacement.reconcile_inputs(ctx.wstore)
    replacement.finish_assignments(ctx.wstore)
    assert tasks.read(ctx.store, 1)["status"] == "unclaimed"
    assert not ctx.wstore.q("SELECT * FROM task_assignments")


def test_slow_transcript_handoff_does_not_hold_chat_admission_lock(tmp_path):
    import threading

    from research_core.watcher.context import build

    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    ctx.hermes.fail(row["run_id"], "retry")
    owner = coordinator(ctx)
    owner.reconcile()
    entered, release = threading.Event(), threading.Event()
    original = ctx.hermes.session_messages

    def history(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    ctx.hermes.session_messages = history

    def advance():
        other = build({"IDE_WORKSPACE": str(ctx.ws.root), "IDE_PLATFORM": "fake"}, hermes=owner)
        try:
            owner.advance_attempts(other)
        finally:
            other.store.close()
            other.wstore.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(advance)
        try:
            assert entered.wait(3)
            sessions.register(
                ctx.wstore, Actor.harness(), session_id="chat", profile="orchestrator", kind="chat"
            )
            run_id = owner.start(
                profile="orchestrator",
                session_id="chat",
                text="Chat works",
                idempotency_key="chat-while-history-stalled",
            )
            assert run_id in ctx.hermes.runs
        finally:
            release.set()
        future.result(timeout=5)
