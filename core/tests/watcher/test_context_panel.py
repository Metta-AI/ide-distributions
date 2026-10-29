"""Step 12b: the agent-context panel is written only on change and keeps the agent's needs."""

from __future__ import annotations

from research_core.store import capabilities, ui_state
from research_core.store._common import now_iso
from research_core.watcher import context_panel
from tests.watcher._support import advance, events_of, make_ctx, make_goal, working_session


def panel(ctx) -> dict:
    return ui_state.get(ctx.wstore, "workspace")["doc"]["panels"]["agent-context"]


def test_panel_written_once_and_only_again_on_change(tmp_path):
    ctx = make_ctx(tmp_path, env={"SOFTMAX_PLAYER_ID": "ply_test"})
    goal = make_goal(ctx)
    ctx.set_meta("heartbeat", now_iso(ctx.clock))

    assert context_panel.run(ctx) == {"written": True}
    shown = panel(ctx)
    assert shown["kind"] == "agent-context" and shown["content"]["needs"] == []
    computed = shown["content"]["computed"]
    assert computed["coworld"]["coworld_id"] == "cow_fake"
    assert computed["player"] == "ply_test" and computed["user_identity_available"] is False
    assert computed["goals"] == [
        {"id": f"G-{goal}", "title": "Reach division 1", "status": "active"}
    ]
    assert computed["heartbeat"] == {"alive": True, "has_work": False}
    assert computed["running_tasks"] == [] and computed["capabilities"] == []

    assert context_panel.run(ctx) == {"written": False}
    advance(ctx, 30)  # time alone changes nothing
    assert context_panel.run(ctx) == {"written": False}
    assert len(events_of(ctx.wstore, "ui.patch")) == 1

    task, session, run_id = working_session(ctx)
    assert context_panel.run(ctx) == {"written": True}
    [running] = panel(ctx)["content"]["computed"]["running_tasks"]
    assert running == {
        "id": task["id"],
        "title": task["title"],
        "type": "research",
        "goal": f"G-{task['goal_id']}",
        "session": session["id"],
        "run": run_id,
        "waiting_on": None,
    }
    assert len(events_of(ctx.wstore, "ui.patch")) == 2


def test_panel_keeps_the_agents_needs_and_other_panels(tmp_path):
    ctx = make_ctx(tmp_path)
    ui_state.patch(
        ctx.wstore,
        ctx.actor,
        "workspace",
        [
            {"op": "add", "path": "/panels", "value": {"notes": {"kind": "markdown"}}},
            {
                "op": "add",
                "path": "/panels/agent-context",
                "value": {
                    "kind": "agent-context",
                    "content": {"computed": {"stale": True}, "needs": ["a Softmax User token"]},
                },
            },
        ],
    )
    assert context_panel.run(ctx) == {"written": True}
    doc = ui_state.get(ctx.wstore, "workspace")["doc"]
    assert doc["panels"]["notes"] == {"kind": "markdown"}
    content = doc["panels"]["agent-context"]["content"]
    assert content["needs"] == ["a Softmax User token"]
    assert "stale" not in content["computed"]


def test_panel_shows_capabilities_with_repair_tasks_and_maintenance_work(tmp_path):
    ctx = make_ctx(tmp_path)
    report = capabilities.report_failure(
        ctx.store, ctx.actor, "build", output="boom", command="sh mixin/tools/build.sh"
    )
    context_panel.run(ctx)
    computed = panel(ctx)["content"]["computed"]
    [capability] = computed["capabilities"]
    assert capability["name"] == "build" and capability["status"] == "broken"
    assert capability["maintenance_task"] == report["maintenance_task"]["id"]
    [maintenance] = computed["goals"]
    assert maintenance["id"] is None and maintenance["title"] == "workspace maintenance"
    assert maintenance["open_tasks"] == [report["maintenance_task"]["id"]]


def test_panel_without_an_active_coworld(tmp_path):
    ctx = make_ctx(tmp_path, store=None, coworld=None)
    with ctx.wstore.tx(ctx.actor) as tx:
        tx.execute("UPDATE coworlds SET active = 0")
    assert context_panel.run(ctx) == {"written": True}
    computed = panel(ctx)["content"]["computed"]
    assert computed["coworld"] is None and computed["goals"] == []
    assert computed["heartbeat"] == {"alive": False, "has_work": False}
