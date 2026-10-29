from datetime import UTC, datetime, timedelta

from research_core.plugin import session_context, turn
from research_core.store import coworlds, notices, sessions
from research_core.store.actors import Actor
from research_core.tools.base import ToolContext

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def make_ctx(tmp_path, session_id="chat1", coworld=True):
    ctx = ToolContext.from_env(
        "orchestrator", {"IDE_WORKSPACE": str(tmp_path), "IDE_SESSION_ID": session_id}
    )
    ctx.workspace.ensure_dirs()
    if coworld:
        harness = Actor.parse("harness")
        coworlds.register(ctx.wstore, harness, "cow_fake", folder="cow_fake")
        coworlds.activate(ctx.wstore, harness, "cow_fake")
        ctx.workspace.coworld("cow_fake").ensure_dirs()
    return ctx


def test_context_block_bad_input_never_crashes():
    assert turn.render_context_block("hello") is None
    assert "not valid JSON" in turn.render_context_block("x\nContext v1:\n{nope")
    text = turn.render_context_block(
        'go\nContext v1:\n{"contract_version": 1, '
        '"mode": {"name": "research", "instruction": "brief"}, '
        '"client_data": {"z": "' + "q" * 9000 + '"}, "bogus": 1, "on_screen": ["G-1"]}'
    )
    assert "mode: research — brief" in text
    assert "on screen: G-1" in text
    assert "ignored:" in text and "client_data" in text and "bogus" in text


def test_notices_appear_once_and_changed_since_note(tmp_path):
    ctx = make_ctx(tmp_path)
    harness = Actor.parse("harness")
    notices.add(ctx.wstore, harness, text="Submitted V-1 to the league", refs=["V-1"])
    first = turn.render_turn(ctx, "chat1", is_first_turn=True, now=NOW)
    assert "[notices since your last turn]" in first and "Submitted V-1" in first
    assert "[active Coworld] cow_fake" in first
    assert "[changed since" not in first  # no earlier turn to compare with

    with ctx.store.tx(harness) as tx:
        tx.event("task.completed", "T-1", {})
        tx.event("task.completed", "T-2", {})
    second = turn.render_turn(ctx, "chat1", is_first_turn=False, now=NOW)
    assert "[notices" not in second
    assert "[changed since your last turn] task.completed ×2" in second


def test_away_summary_only_after_an_hour_on_a_new_chat(tmp_path):
    ctx = make_ctx(tmp_path)
    harness = Actor.parse("harness")
    turn.render_turn(ctx, "old-chat", is_first_turn=True, now=NOW)
    with ctx.store.tx(harness) as tx:
        tx.event("version.submitted", "V-3", {})
    fresh = turn.render_turn(ctx, "new-chat", is_first_turn=True, now=NOW + timedelta(minutes=10))
    assert "while you were away" not in fresh
    with ctx.store.tx(harness) as tx:
        tx.event("version.submitted", "V-4", {})
    # Events carry the real clock, so the recorded last turn is "now"; two hours later:
    later = turn.render_turn(
        ctx, "newer-chat", is_first_turn=True, now=datetime.now(UTC) + timedelta(hours=2)
    )
    assert "while you were away" in later and "Submissions: 1" in later


def test_pre_llm_call_only_for_orchestrator_chat_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("IDE_WORKSPACE", str(tmp_path))
    ctx = make_ctx(tmp_path)
    sessions.register(
        ctx.wstore, Actor.parse("harness"), session_id="wake1", profile="orchestrator", kind="wake"
    )
    hook = turn.make_pre_llm_call("orchestrator")
    assert hook(session_id="wake1", user_message="x", is_first_turn=True) is None
    result = hook(session_id="chat9", user_message="hello", is_first_turn=True)
    assert result and "[active Coworld] cow_fake" in result["context"]
    assert sessions.read(ctx.wstore, "chat9")["kind"] == "chat"
    assert turn.make_pre_llm_call("researcher")(session_id="chat9", user_message="x") is None


def test_session_context_section(tmp_path):
    ctx = make_ctx(tmp_path, coworld=False)
    sessions.register(
        ctx.wstore,
        Actor.parse("harness"),
        session_id="chat1",
        profile="orchestrator",
        kind="chat",
        session_context={"mode": {"name": "research"}},
        client_capabilities={"views": ["replay"]},
    )
    text = session_context.render(ctx, "chat1")
    assert text.startswith("[session context]") and "outrank" in text
    assert '"research"' in text and '"replay"' in text and len(text) <= 3800
    none = session_context.render(ctx, "unknown")
    assert "No session context" in none and "No client capabilities" in none
    assert session_context.make_section("researcher")({"session_id": "chat1"}) == ""


def test_reactions_reach_the_agent_at_its_next_turn(tmp_path):
    from research_core.store import ui_state

    ctx = make_ctx(tmp_path)
    turn.render_turn(ctx, "chat1", is_first_turn=True, now=NOW)
    user = Actor.user("u1")
    ui_state.patch(
        ctx.wstore,
        user,
        "chat1",
        [
            {"op": "add", "path": "/reactions", "value": {}},
            {"op": "add", "path": "/reactions/m-7", "value": {"value": "👍", "by": "user:u1"}},
        ],
    )
    ui_state.patch(
        ctx.wstore,
        user,
        "other-chat",
        [
            {"op": "add", "path": "/reactions", "value": {}},
            {"op": "add", "path": "/reactions/m-9", "value": {"value": "👎", "by": "user:u1"}},
        ],
    )
    text = turn.render_turn(ctx, "chat1", is_first_turn=False, now=NOW)
    assert "[reactions since your last turn]\n- message m-7: 👍" in text
    assert "m-9" not in text  # another conversation's reaction
    assert "ui.patch" not in text  # not counted as a changed-since kind
    assert "[reactions" not in turn.render_turn(ctx, "chat1", is_first_turn=False, now=NOW)
