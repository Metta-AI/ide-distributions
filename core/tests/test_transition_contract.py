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


def test_slow_known_stop_retains_ownership_without_runtime_exclusion(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = Coordinator(ctx.ws, ctx.hermes, "same-runtime")
    sessions.register(
        ctx.wstore, Actor.harness(), session_id="unrelated", profile="orchestrator", kind="chat"
    )
    unrelated = owner.start(
        profile="orchestrator",
        session_id="unrelated",
        text="Independent work",
        idempotency_key="other",
    )
    stops = []
    ctx.hermes.stop = lambda run_id, **kwargs: stops.append(run_id)
    with ctx.wstore.tx(Actor.harness()) as tx:
        tx.execute("UPDATE client_admissions SET runtime_id='same-runtime'")
        tx.execute(
            "UPDATE task_assignments SET state='release_requested',"
            "recovery_deadline='2000-01-01T00:00:00Z'"
        )
    for _ in range(3):
        owner.finish_assignments(ctx.wstore)
    assert stops == [row["run_id"]] * 3
    assert not admissions.restart_required(ctx.wstore)
    assert tasks.read(ctx.store, 1)["status"] == "in-progress"
    assert ctx.hermes.status(unrelated, profile="orchestrator").status == "running"
    [visible] = [w for w in admissions.work_status(ctx.wstore) if w["attempt_id"] == row["op_id"]]
    assert visible["state"] == "waiting" and "stopping" in visible["reason"]
    assert visible["owner"].startswith("native:") and visible["trigger"]
    ctx.hermes.complete(row["run_id"])
    owner.finish_assignments(ctx.wstore)
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


@pytest.mark.parametrize("lost_reply", [False, True])
def test_release_before_start_receipt_retains_execution_authority(tmp_path, lost_reply):
    from types import SimpleNamespace

    from research_core.watcher.hermes_api import RunsUnavailable

    ctx = make_ctx(tmp_path)
    owner = Coordinator(ctx.ws, ctx.hermes, "original-runtime")
    original = ctx.hermes.start
    accepted = []

    def start(**kwargs):
        run_id = original(**kwargs)
        accepted.append(run_id)
        with (
            db.Store.open(ctx.ws.store_path, kind="workspace") as wstore,
            db.Store.open(ctx.coworld.store_path, kind="coworld") as store,
        ):
            row = wstore.one("SELECT * FROM client_admissions")
            with wstore.tx(Actor.harness()) as tx:
                tx.execute("UPDATE client_admissions SET runtime_id='original-runtime'")
            command = SimpleNamespace(
                env=ctx.env,
                store=store,
                wstore=wstore,
                actor=Actor.session(row["session_id"]),
                coworld=lambda: ctx.coworld,
                hermes_session_id=lambda: row["session_id"],
            )
            owner.request_release(command, 1, "finished before receipt")
            owner.finish_assignments(wstore)
            assert tasks.read(store, 1)["held_by"] == row["session_id"]
            assert wstore.one("SELECT count(*) FROM task_assignments")[0] == 1
            assert ctx.hermes.status(run_id, profile="researcher").status == "running"
        if lost_reply:
            raise RunsUnavailable("start reply lost")
        return run_id

    ctx.hermes.start = start
    assigned(ctx)
    row = ctx.wstore.one("SELECT * FROM client_admissions")
    if lost_reply:
        assert row["run_id"] is None
        # A new coordinator in the same private namespace cannot release after a crash.
        owner = Coordinator(ctx.ws, ctx.hermes, "original-runtime")
        owner.finish_assignments(ctx.wstore)
        assert ctx.wstore.one("SELECT count(*) FROM task_assignments")[0] == 1
        with ctx.wstore.tx(Actor.harness()) as tx:
            tx.execute("UPDATE task_assignments SET recovery_deadline='2000-01-01T00:00:00Z'")
        owner.finish_assignments(ctx.wstore)
        assert ctx.wstore.one("SELECT execution_uncertain FROM client_admissions")[0] == 1
        assert ctx.wstore.one("SELECT count(*) FROM task_assignments")[0] == 1
        replacement = Coordinator(ctx.ws, ctx.hermes, "replacement-runtime")
        replacement.reconcile_inputs(ctx.wstore)
        replacement.finish_assignments(ctx.wstore)
    else:
        assert row["run_id"] == accepted[0]
        owner.finish_assignments(ctx.wstore)  # stop request is not termination evidence
        assert tasks.read(ctx.store, 1)["status"] == "in-progress"
        owner.finish_assignments(ctx.wstore)
    assert tasks.read(ctx.store, 1)["status"] == "unclaimed"
    assert not ctx.wstore.q("SELECT * FROM task_assignments")


@pytest.mark.parametrize("rejection", ["auth"])
@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.parametrize("with_messages", [False, True])
def test_rejected_event_guidance_has_bounded_durable_redelivery(
    tmp_path, permanent, with_messages, rejection
):
    from research_core.store import events
    from research_core.watcher.hermes_api import RunsAuthError, RunsBusy, RunsUnavailable

    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    set_setting(ctx, "recovery.max_attempts", 3)
    reject = True
    steer = ctx.hermes.steer

    def rejected(run_id, text, *, profile):
        if reject:
            if rejection == "auth":
                raise RunsAuthError("Credentials rejected", status=403)
            if rejection == "busy":
                raise RunsBusy("Rate limited", status=429)
            if rejection == "draining":
                raise RunsUnavailable("Gateway draining", status=503, code="gateway_draining")
            return False
        return steer(run_id, text, profile=profile)

    ctx.hermes.steer = rejected
    with ctx.store.tx(Actor.harness()) as tx:
        seq = tx.event("task.wait_resolved", "T-1", {"kind": "user", "note": "approved"})
    if with_messages:
        tasks.message(
            ctx.store, Actor.harness(), 1, text="Retain this guidance", direction="to_researcher"
        )
    owner.advance_events(ctx)
    assert not events.unhandled(ctx.store, {"task.wait_resolved"})
    assert ctx.store.one("SELECT count(*) FROM handled_events WHERE seq=?", (seq,))[0] == 1
    dispatch_inputs(ctx)
    owner.deliver_worker_messages(ctx)  # A second producer runs before failed-row advancement.
    assert (
        ctx.wstore.one("SELECT count(*) FROM client_admissions WHERE origin='continuation'")[0] == 1
    )
    rejected_row = ctx.wstore.one("SELECT * FROM client_admissions WHERE origin='continuation'")
    assert rejected_row["status"] == "failed" and not rejected_row["dispatch_started"]
    assert bool(json.loads(rejected_row["delivery_ids"])) == with_messages
    if not permanent:
        reject = False
    for _ in range(8):
        owner.advance_attempts(ctx)
        owner.advance_events(ctx)
        owner.deliver_worker_messages(ctx)
        dispatch_inputs(ctx)
    deliveries = ctx.wstore.q("SELECT * FROM client_admissions WHERE origin='continuation'")
    assert len(ctx.hermes.runs) == 1
    assert bool(tasks.pending_messages(ctx.store, 1, "to_researcher")) == (
        permanent and with_messages
    )
    if permanent:
        assert len(deliveries) == 3
        assert all(attempt["status"] == "failed" for attempt in deliveries)
        assert deliveries[-1]["disposition"] == "delivery retries exhausted"
        assert deliveries[-1]["advanced_at"] is not None
        assert deliveries[-1]["successor_id"] is None
        reject = False
        for _ in range(3):
            owner.advance_attempts(ctx)
            owner.deliver_worker_messages(ctx)
            dispatch_inputs(ctx)
        assert (
            ctx.wstore.one("SELECT count(*) FROM client_admissions WHERE origin='continuation'")[0]
            == 3
        )
        assert not ctx.hermes.steered.get(row["run_id"])
    else:
        assert len(deliveries) == 2
        assert len(ctx.hermes.steered[row["run_id"]]) == 1
        assert ctx.hermes.steered[row["run_id"]][0].startswith(
            "Your wait on user resolved: approved. Continue."
        )
        if with_messages:
            assert ctx.hermes.steered[row["run_id"]][0].count("Retain this guidance") == 1
        assert deliveries[0]["successor_id"] == deliveries[1]["op_id"]
        assert deliveries[0]["disposition"] == "delivery retry queued"
        assert deliveries[1]["delivery_failures"] == 1
    assert ctx.store.one("SELECT count(*) FROM handled_events WHERE seq=?", (seq,))[0] == 1


def test_uncertain_event_guidance_is_not_replayed(tmp_path):
    from research_core.watcher.hermes_api import RunsUnavailable

    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    steer = ctx.hermes.steer

    def reply_lost(run_id, text, *, profile):
        steer(run_id, text, profile=profile)
        raise RunsUnavailable("Steer reply lost after acceptance")

    ctx.hermes.steer = reply_lost
    with ctx.store.tx(Actor.harness()) as tx:
        tx.event("task.wait_resolved", "T-1", {"kind": "user", "note": "approved"})
    owner.advance_events(ctx)
    for _ in range(5):
        dispatch_inputs(ctx)
        owner.advance_attempts(ctx)
        owner.advance_events(ctx)
    assert len(ctx.hermes.steered[row["run_id"]]) == 1
    [delivery] = ctx.wstore.q("SELECT * FROM client_admissions WHERE origin='continuation'")
    assert delivery["dispatch_started"] and delivery["status"] == "interrupted"
    assert delivery["successor_id"] is None


def test_target_end_redelivery_does_not_spend_zero_failure_budget(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    session = sessions.read(ctx.wstore, row["session_id"])
    owner.continue_session(ctx, session, "Guidance", key="original-event")
    with ctx.wstore.tx(Actor.harness()) as tx:
        tx.execute(
            "UPDATE client_admissions SET status='cancelled',"
            "disposition='target ended before delivery' WHERE native_key='original-event'"
        )
    set_setting(ctx, "recovery.max_attempts", 0)
    result = owner.continue_session(ctx, session, "Guidance", key="original-event")
    assert result["key"] == "original-event:after-end"
    dispatch_inputs(ctx)
    assert ctx.hermes.steered[row["run_id"]] == ["Guidance"]
    assert (
        ctx.wstore.one(
            "SELECT delivery_failures FROM client_admissions WHERE native_key=?", (result["key"],)
        )[0]
        == 0
    )


@pytest.mark.parametrize("kind", ["false", "busy", "draining"])
@pytest.mark.parametrize("with_messages", [False, True])
def test_transient_steer_rejections_wait_with_visible_backoff(tmp_path, kind, with_messages):
    from research_core.watcher.hermes_api import RunsBusy, RunsUnavailable

    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    ctx.wstore.set_meta("coordinator_instance", owner.instance_id)
    if with_messages:
        tasks.message(
            ctx.store, Actor.harness(), 1, text="Keep guidance", direction="to_researcher"
        )
    with ctx.store.tx(Actor.harness()) as tx:
        tx.event("task.wait_resolved", "T-1", {"kind": "user", "note": "approved"})
    calls = []
    steer = ctx.hermes.steer
    reject = True

    def transient(run_id, text, *, profile):
        calls.append(run_id)
        if reject:
            if kind == "busy":
                raise RunsBusy("Rate limited", status=429)
            if kind == "draining":
                raise RunsUnavailable("Gateway draining", status=503, code="gateway_draining")
            return False
        return steer(run_id, text, profile=profile)

    ctx.hermes.steer = transient
    owner.advance_events(ctx)
    for _ in range(4):  # More transient refusals than the ordinary failure budget.
        dispatch_inputs(ctx)
        [delivery] = ctx.wstore.q("SELECT * FROM client_admissions WHERE origin='continuation'")
        assert delivery["status"] == "queued" and delivery["delivery_failures"] == 0
        assert delivery["retry_at"] and not delivery["dispatch_started"]
        [waiting] = [
            w for w in admissions.work_status(ctx.wstore) if w["attempt_id"] == delivery["op_id"]
        ]
        assert waiting["state"] == "waiting" and waiting["owner"].startswith("coordinator:")
        assert delivery["retry_at"] in waiting["trigger"]
        attempts = len(calls)
        owner.advance_attempts(ctx)
        owner.deliver_worker_messages(ctx)
        dispatch_inputs(ctx)
        assert len(calls) == attempts  # Polling before the deadline cannot hammer the gateway.
        with ctx.wstore.tx(Actor.harness()) as tx:
            tx.execute(
                "UPDATE client_admissions SET retry_at='2000-01-01T00:00:00Z' WHERE op_id=?",
                (delivery["op_id"],),
            )
    reject = False
    dispatch_inputs(ctx)
    dispatch_inputs(ctx)
    assert not tasks.pending_messages(ctx.store, 1, "to_researcher")
    assert len(ctx.hermes.steered[row["run_id"]]) == 1
    [delivery] = ctx.wstore.q("SELECT * FROM client_admissions WHERE origin='continuation'")
    assert delivery["status"] == "running" and delivery["retry_at"] is None


def test_abandoned_task_cancels_queued_successor_without_dispatch_crash(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    ctx.hermes.fail(row["run_id"], "crash")
    owner = coordinator(ctx)
    owner.reconcile()
    owner.advance_attempts(ctx)
    tasks.abandon(
        ctx.store, Actor.harness(), 1, failure={"what_failed": "abandoned", "next_step": "review"}
    )
    dispatch_inputs(ctx)
    dispatch_inputs(ctx)
    successor = ctx.wstore.one("SELECT * FROM client_admissions WHERE origin='continuation'")
    assert successor["status"] == "cancelled" and successor["advanced_at"]
    assert successor["disposition"] == "task ownership ended before transfer"
    assert not ctx.wstore.q("SELECT * FROM task_assignments")
    assert len(ctx.hermes.runs) == 1


def test_settled_or_inactive_attempts_do_not_fetch_transcripts(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    ctx.hermes.complete(row["run_id"])
    tasks.wait(ctx.store, Actor.session(row["session_id"]), 1, kind="user", detail="planned wait")
    owner.reconcile()
    calls = []
    ctx.hermes.session_messages = lambda *args, **kwargs: calls.append(args) or []
    owner.advance_attempts(ctx)
    assert calls == []
    with ctx.wstore.tx(Actor.harness()) as tx:
        tx.execute("UPDATE client_admissions SET advanced_at=NULL")
    ctx.coworld = None
    for _ in range(3):
        owner.advance_attempts(ctx)
    assert calls == []


def test_retry_transcript_omits_whole_messages_to_preserve_json(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    owner = coordinator(ctx)
    ctx.hermes.fail(row["run_id"], "crash")
    owner.reconcile()
    ctx.hermes.session_messages = lambda *args, **kwargs: [
        {"role": "user", "content": "x" * 40000},
        {"role": "assistant", "content": "recent"},
    ]
    owner.advance_attempts(ctx)
    successor = ctx.wstore.one("SELECT * FROM client_admissions WHERE origin='continuation'")
    text = json.loads(successor["payload"])["input"]
    encoded = text.split("32000 characters):\n", 1)[1].split("\n\n", 1)[0]
    assert json.loads(encoded) == [{"role": "assistant", "content": "recent"}]


def test_work_status_omits_acknowledged_history(tmp_path):
    ctx = make_ctx(tmp_path)
    row = assigned(ctx)
    ctx.hermes.complete(row["run_id"])
    tasks.wait(ctx.store, Actor.session(row["session_id"]), 1, kind="user", detail="planned wait")
    owner = coordinator(ctx)
    owner.reconcile()
    owner.advance_attempts(ctx)
    assert admissions.work_status(ctx.wstore) == []
