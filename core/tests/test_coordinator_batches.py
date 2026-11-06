"""Mainline champion batching uses the coordinator's ordinary durable handoffs."""

from research_core.store import events, sessions
from research_core.watcher import wakes
from tests.watcher._support import HARNESS, coordinator, dispatch_inputs, make_ctx


def changes(ctx, *subjects):
    with ctx.store.tx(HARNESS) as tx:
        return [
            tx.event("champion.changed", subject, {"policy_version_id": subject})
            for subject in subjects
        ]


def test_champion_changes_batch_and_later_events_wait(tmp_path):
    ctx = make_ctx(tmp_path)
    seqs = changes(ctx, "league_a", "league_b", "league_c")
    owner = coordinator(ctx)
    owner.advance_events(ctx)
    dispatch_inputs(ctx)
    [run] = ctx.hermes.runs.values()
    assert all(subject in run.input for subject in ("league_a", "league_b", "league_c"))
    assert [row["session_id"] for row in ctx.store.q("SELECT session_id FROM handled_events")] == [
        run.session_id
    ] * 3
    [late] = changes(ctx, "league_d")
    owner.advance_events(ctx)
    assert len(ctx.hermes.runs) == 1
    assert ctx.store.one("SELECT * FROM handled_events WHERE seq=?", (late,)) is None
    ctx.hermes.complete(run.run_id)
    owner.reconcile()
    owner.advance_attempts(ctx)
    assert [event.seq for event in events.unhandled(ctx.store, {"champion.changed"})] == [late]
    owner.advance_events(ctx)
    dispatch_inputs(ctx)
    last = list(ctx.hermes.runs.values())[-1]
    assert "league_d" in last.input and "league_a" not in last.input
    assert all(
        ctx.store.one("SELECT handled_at FROM handled_events WHERE seq=?", (seq,))[0]
        for seq in seqs
    )


def test_batch_retries_together_and_busy_does_not_burn_claim(tmp_path):
    ctx = make_ctx(tmp_path)
    seqs = changes(ctx, "league_a", "league_b")
    owner = coordinator(ctx)
    owner.advance_events(ctx)
    dispatch_inputs(ctx)
    [first] = ctx.hermes.runs.values()
    ctx.hermes.fail(first.run_id, "model failed")
    owner.reconcile()
    owner.advance_attempts(ctx)
    owner.advance_events(ctx)
    ctx.hermes.set_busy(True)
    dispatch_inputs(ctx)
    owner.advance_events(ctx)
    assert [row["attempts"] for row in ctx.store.q("SELECT attempts FROM handled_events")] == [2, 2]
    ctx.hermes.set_busy(False)
    # The refusal backs off for recovery.delivery_backoff_seconds; let that pass.
    with ctx.wstore.tx(HARNESS) as tx:
        tx.execute("UPDATE client_admissions SET retry_at=NULL")
    dispatch_inputs(ctx)
    assert len(ctx.hermes.runs) == 2
    last = list(ctx.hermes.runs.values())[-1]
    assert last.idempotency_key == f"event:cow_fake:{seqs[0]}:2"
    assert all(subject in last.input for subject in ("league_a", "league_b"))


def test_batch_exhaustion_is_declared_once_per_event(tmp_path):
    ctx = make_ctx(tmp_path)
    changes(ctx, "league_a", "league_b")
    owner = coordinator(ctx)
    for _ in range(2):
        owner.advance_events(ctx)
        dispatch_inputs(ctx)
        run = list(ctx.hermes.runs.values())[-1]
        ctx.hermes.fail(run.run_id, "model failed")
        owner.reconcile()
        owner.advance_attempts(ctx)
    owner.advance_events(ctx)
    assert ctx.wstore.one("SELECT count(*) FROM events WHERE kind='wake.failed'")[0] == 2
    assert (
        ctx.wstore.one(
            "SELECT count(*) FROM client_admissions WHERE native_key LIKE 'event:cow_fake:%'"
        )[0]
        == 2
    )


def test_interrupted_batch_registration_reuses_claim_identity(tmp_path):
    ctx = make_ctx(tmp_path)
    seqs = changes(ctx, "league_a", "league_b")
    for seq in seqs:
        events.claim_handling(ctx.store, HARNESS, seq, "champion.changed", "interrupted-batch")
    coordinator(ctx).advance_events(ctx)
    [admission] = ctx.wstore.q("SELECT * FROM client_admissions")
    assert admission["session_id"] == "interrupted-batch"
    assert [row["attempts"] for row in ctx.store.q("SELECT attempts FROM handled_events")] == [1, 1]
    assert sessions.read(ctx.wstore, "interrupted-batch")["event"] == "champion.changed"


def test_batch_render_preserves_small_payload_after_large_ones(tmp_path):
    ctx = make_ctx(tmp_path)
    with ctx.store.tx(HARNESS) as tx:
        for subject in ("league_a", "league_b"):
            tx.event("champion.changed", subject, {"value": "x" * 9000})
        tx.event("champion.changed", "league_c", {"policy_version_id": "pv_c"})
    batch = events.unhandled(ctx.store, {"champion.changed"})
    text = wakes.render_batch("management", batch, ctx)
    assert text.count("payload cut here") == 2
    assert '"policy_version_id": "pv_c"' in text
    assert "league_a, league_b, league_c" in text


def test_failed_tasks_share_one_wake(tmp_path):
    """An outage can fail many Tasks at once; that must not start a management run for each."""
    ctx = make_ctx(tmp_path)
    with ctx.store.tx(HARNESS) as tx:
        for task in ("T-1", "T-2", "T-3"):
            tx.event("task.failure_review", task, {"task": task, "what_failed": f"{task} broke"})
    coordinator(ctx).advance_events(ctx)
    dispatch_inputs(ctx)
    [run] = ctx.hermes.runs.values()
    assert all(f"{task} broke" in run.input for task in ("T-1", "T-2", "T-3"))
