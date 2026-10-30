"""Wake dispatch (step 11): idempotency, acknowledgment, retries, joining, intents, handlers."""

from __future__ import annotations

from research_core.store import events, intents, notices, sessions
from research_core.store._common import parse_display_id
from research_core.watcher import handlers, store_checks, wakes
from research_core.watcher.handlers import Handler
from research_core.watcher.hermes_api import FakeHermesRuns
from tests.watcher._support import HARNESS, advance, events_of, make_ctx, set_setting


def emit(store, kind, subject, payload=None) -> int:
    with store.tx(HARNESS) as tx:
        return tx.event(kind, subject, payload or {})


def starts(ctx) -> list[dict]:
    return [kwargs for name, kwargs in ctx.hermes.calls if name == "start"]


def handled_row(store, seq):
    return store.one("SELECT * FROM handled_events WHERE seq = ?", (seq,))


def run_id_of(ctx, session_id: str) -> str:
    return sessions.read(ctx.wstore, session_id)["run_id"]


def test_wake_dispatch_idempotent(tmp_path):
    ctx = make_ctx(tmp_path)
    assert not wakes.has_queued_wakes(ctx)
    seq = emit(ctx.store, "task.asked", "T-1", {"question": "which seed?"})
    assert wakes.has_queued_wakes(ctx)

    first = wakes.run(ctx)
    [start] = starts(ctx)
    assert first["started"] == [f"cow_fake:{seq}->{start and run_id_of(ctx, start['session_id'])}"]
    assert start["profile"] == "orchestrator"
    assert start["instructions"] == wakes.WAKE_INSTRUCTIONS
    assert start["idempotency_key"] == f"event:cow_fake:{seq}:1"
    for expected in ("task.asked", "T-1", "which seed?", "cow_fake", "ui.notify"):
        assert expected in start["text"], expected
    session = sessions.read(ctx.wstore, start["session_id"])
    assert session["kind"] == "wake" and session["event"] == "task.asked"
    assert session["task_id"] == 1 and session["coworld_id"] == "cow_fake"
    run_id = session["run_id"]
    [intent] = [i for i in events_of(ctx.store, "intent.done")]
    assert intent["payload"]["key"] == f"{session['id']}:1"
    assert intent["payload"]["result"] == {"run_id": run_id}

    # A second tick starts nothing: the run is live and the event is still unacknowledged.
    second = wakes.run(ctx)
    assert len(starts(ctx)) == 1 and second["waiting"] == [f"cow_fake:{seq}"]
    assert handled_row(ctx.store, seq)["handled_at"] is None
    assert wakes.has_queued_wakes(ctx)

    ctx.hermes.complete(run_id, "answered the question")
    third = wakes.run(ctx)
    assert third["acknowledged"] == [f"cow_fake:{seq}"]
    row = handled_row(ctx.store, seq)
    assert row["handled_at"] is not None and row["run_id"] == run_id and row["attempts"] == 1
    assert sessions.read(ctx.wstore, session["id"])["ended"] is not None
    assert events.unhandled(ctx.store, {"task.asked"}) == []
    assert not wakes.has_queued_wakes(ctx)
    assert len(starts(ctx)) == 1


def test_failed_run_retries_with_a_new_session_then_gives_up(tmp_path):
    ctx = make_ctx(tmp_path)  # recovery.max_attempts defaults to 2
    seq = emit(ctx.store, "task.proposed", "T-2", {"title": "Try a wider search"})
    wakes.run(ctx)
    first = starts(ctx)[-1]
    ctx.hermes.fail(run_id_of(ctx, first["session_id"]), "model error")

    report = wakes.run(ctx)
    assert report["run_failed"] == [f"cow_fake:{seq}"]
    second = starts(ctx)[-1]
    assert len(starts(ctx)) == 2 and second["session_id"] != first["session_id"]
    assert second["idempotency_key"] == f"event:cow_fake:{seq}:2"
    row = handled_row(ctx.store, seq)
    assert row["attempts"] == 2 and row["handled_at"] is None
    assert sessions.read(ctx.wstore, first["session_id"])["ended"] is not None
    ctx.hermes.fail(run_id_of(ctx, second["session_id"]), "model error again")

    report = wakes.run(ctx)
    assert report["gave_up"] == [f"cow_fake:{seq}"]
    assert len(starts(ctx)) == 2  # no third try for this event
    row = handled_row(ctx.store, seq)
    assert row["handled_at"] is None and row["attempts"] == 3
    assert "gave up after 2" in row["last_error"] and "model error again" in row["last_error"]
    [failed] = events_of(ctx.wstore, "wake.failed")
    assert failed["payload"]["seq"] == seq and failed["payload"]["kind"] == "task.proposed"
    assert failed["payload"]["wake"] == "management" and failed["payload"]["store"] == "cow_fake"
    assert any(
        "could not complete the management wake" in n["text"] for n in notices.unread(ctx.wstore)
    )
    assert not wakes.has_queued_wakes(ctx) or events.unhandled(ctx.wstore, {"wake.failed"})

    # Once: the next tick neither retries nor reports again, and the wake.failed event gets
    # its own management wake (it is in the ownership map).
    report = wakes.run(ctx)
    assert report["gave_up"] == [f"cow_fake:{seq}"]
    assert len(events_of(ctx.wstore, "wake.failed")) == 1
    assert len(notices.unread(ctx.wstore)) == 1
    keys = [s["idempotency_key"] for s in starts(ctx)]
    assert keys.count(f"event:cow_fake:{seq}:1") == 1 and keys.count(f"event:cow_fake:{seq}:2") == 1
    assert [k for k in keys if k.startswith("event:workspace:")] == [
        f"event:workspace:{failed['seq']}:1"
    ]
    assert "wake.failed" in starts(ctx)[-1]["text"]


def test_a_wake_failed_event_that_fails_ends_in_a_notice_not_another_event(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "recovery.max_attempts", 0)
    seq = emit(ctx.wstore, "wake.failed", "cow_fake:9", {"kind": "task.asked"})
    report = wakes.run(ctx)
    assert report["gave_up"] == [f"workspace:{seq}"] and starts(ctx) == []
    assert events_of(ctx.wstore, "wake.failed") == events_of(ctx.wstore, "wake.failed")[:1]
    assert len(notices.unread(ctx.wstore)) == 1


def test_run_intent_written_before_start(tmp_path):
    class CrashingRuns(FakeHermesRuns):
        def start(self, **kwargs):
            raise RuntimeError("socket closed mid-request")

    ctx = make_ctx(tmp_path, hermes=CrashingRuns())
    seq = emit(ctx.store, "task.asked", "T-1", {"question": "?"})
    report = wakes.run(ctx)
    assert len(report["errors"]) == 1 and "socket closed" in report["errors"][0]
    [intent] = intents.pending(ctx.store, "run")
    session_id = handled_row(ctx.store, seq)["session_id"]
    assert intent["key"] == f"{session_id}:1" and intent["subject"] == session_id
    session = sessions.read(ctx.wstore, session_id)
    assert session["run_id"] is None and session["ended"] is None

    # The next tick retries the same Session and attempt with the same idempotency key, so a
    # start that did reach the gateway is replayed rather than repeated.
    ctx.hermes = FakeHermesRuns()
    report = wakes.run(ctx)
    [start] = starts(ctx)
    assert start["session_id"] == session_id
    assert start["idempotency_key"] == f"event:cow_fake:{seq}:1"
    assert handled_row(ctx.store, seq)["attempts"] == 1
    assert intents.pending(ctx.store, "run") == []
    _, number = parse_display_id(intent["id"])
    finished = intents.read(ctx.store, number)
    assert finished["state"] == "done"
    assert finished["result"] == {"run_id": run_id_of(ctx, session_id)}


def test_busy_gateway_defers_without_burning_an_attempt(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.hermes.set_busy(True)
    seq = emit(ctx.store, "task.asked", "T-1", {})
    report = wakes.run(ctx)
    assert report["busy"] == [f"cow_fake:{seq}"] and report["started"] == []
    row = handled_row(ctx.store, seq)
    assert row["attempts"] == 1 and sessions.read(ctx.wstore, row["session_id"])["run_id"] is None
    assert any("deferred" in note for note in ctx.notes)
    assert wakes.has_queued_wakes(ctx)

    ctx.hermes.set_busy(False)
    wakes.run(ctx)
    assert handled_row(ctx.store, seq)["attempts"] == 1
    last = starts(ctx)[-1]
    assert last["session_id"] == row["session_id"]
    assert last["idempotency_key"] == f"event:cow_fake:{seq}:1"
    assert run_id_of(ctx, row["session_id"])


def test_repeat_within_six_hours_joins_the_open_wake(tmp_path):
    ctx = make_ctx(tmp_path)
    first = emit(ctx.store, "goal.needs_ideation", "G-1", {})
    wakes.run(ctx)
    [start] = starts(ctx)
    session_id = start["session_id"]
    assert sessions.read(ctx.wstore, session_id)["goal_id"] == 1

    advance(ctx, 3600)
    second = emit(ctx.store, "goal.needs_ideation", "G-1", {})
    report = wakes.run(ctx)
    assert len(starts(ctx)) == 1 and report["joined"] == [f"cow_fake:{second}->{session_id}"]
    assert handled_row(ctx.store, second)["session_id"] == session_id

    advance(ctx, 6 * 3600)  # the first wake is still live but older than the window
    third = emit(ctx.store, "goal.needs_ideation", "G-1", {})
    wakes.run(ctx)
    assert len(starts(ctx)) == 2 and starts(ctx)[-1]["session_id"] != session_id

    ctx.hermes.complete(run_id_of(ctx, session_id), "approved an ideation Task")
    wakes.run(ctx)
    assert handled_row(ctx.store, first)["handled_at"]
    assert handled_row(ctx.store, second)["handled_at"]
    assert handled_row(ctx.store, third)["handled_at"] is None


def test_no_model_handler_is_called_and_acknowledged(tmp_path, monkeypatch):
    calls: list[tuple] = []

    def handle_ok(ctx, event, store):
        calls.append((event.kind, event.subject, store.kind))

    def handle_boom(ctx, event, store):
        raise RuntimeError("boom")

    monkeypatch.setattr(store_checks, "handle_ok", handle_ok, raising=False)
    monkeypatch.setattr(store_checks, "handle_boom", handle_boom, raising=False)
    prefix = "research_core.watcher."
    for kind, path in (
        ("task.failed", prefix + "store_checks:handle_ok"),
        ("task.stuck", prefix + "store_checks:handle_boom"),
        ("session.dead", prefix + "not_yet_built:handle_dead"),
    ):
        monkeypatch.setitem(handlers.HANDLERS, kind, Handler(kind, path, None, False))
    ctx = make_ctx(tmp_path)
    ok = emit(ctx.store, "task.failed", "T-1", {})
    boom = emit(ctx.store, "task.stuck", "T-2", {})
    dead = emit(ctx.wstore, "session.dead", "api_1_dead", {})

    report = wakes.run(ctx)
    assert calls == [("task.failed", "T-1", "coworld")]
    assert report["handled"] == [f"cow_fake:{ok}"] and report["failed"] == [f"cow_fake:{boom}"]
    row = handled_row(ctx.store, ok)
    assert row["handled_at"] is not None and row["run_id"] is None
    row = handled_row(ctx.store, boom)
    assert row["handled_at"] is None and row["last_error"] == "RuntimeError: boom"
    assert handled_row(ctx.wstore, dead) is None and report["not_built"] == [f"workspace:{dead}"]
    assert starts(ctx) == []
    assert not wakes.has_queued_wakes(ctx)  # function-handled kinds are not wakes


def test_workspace_event_without_an_active_coworld_skips_the_intent(tmp_path):
    ctx = make_ctx(tmp_path, store=None, coworld=None)
    request = {"text": "submit the starter as my champion", "cites": "msg-1"}
    seq = emit(ctx.wstore, "coworld.setup_needed", "cow_new", {"pending_request": request})
    report = wakes.run(ctx)
    [start] = starts(ctx)
    assert report["started"] and start["idempotency_key"] == f"event:workspace:{seq}:1"
    assert "submit the starter" in start["text"] and "pending_request" in start["text"]
    assert "none active" in start["text"]
    assert any("run intent" in note for note in ctx.notes)
    session = sessions.read(ctx.wstore, start["session_id"])
    assert session["coworld_id"] is None and session["run_id"]
    assert wakes.has_queued_wakes(ctx)
    ctx.hermes.complete(session["run_id"], "set up")
    wakes.run(ctx)
    assert handled_row(ctx.wstore, seq)["handled_at"] and not wakes.has_queued_wakes(ctx)


def test_render_wake_cuts_a_huge_payload(tmp_path):
    ctx = make_ctx(tmp_path)
    seq = emit(ctx.store, "store.unmarked_write", "cow_fake", {"rows": ["x" * 50] * 200})
    [event] = [e for e in events.unhandled(ctx.store, {"store.unmarked_write"}) if e.seq == seq]
    text = wakes.render_wake("management", event, ctx)
    assert "payload cut here" in text and len(text) < wakes.PAYLOAD_CHARS + 4000
