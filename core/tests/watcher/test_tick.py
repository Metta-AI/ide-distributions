"""The tick skeleton: step order, pause, step isolation, store checks, heartbeat, the command."""

from __future__ import annotations

import json
import sqlite3
import sys
import types

from research_core.store import notices, settings
from research_core.store._common import now_iso
from research_core.store.db import Store
from research_core.watcher import tick as tick_module
from research_core.watcher.tick import PAUSED_STEPS, STEP_ORDER, tick
from tests.watcher._support import HARNESS, advance, events_of, make_ctx, set_setting

BACKGROUND_STEPS = [name for name in STEP_ORDER if name not in PAUSED_STEPS]


def emit(store, kind, subject, payload=None) -> int:
    with store.tx(HARNESS) as tx:
        return tx.event(kind, subject, payload or {})


def starts(ctx) -> list[dict]:
    return [kwargs for name, kwargs in ctx.hermes.calls if name == "start"]


def fake_step(monkeypatch, name: str, run) -> None:
    """Stand a module in for ``research_core.watcher.<name>`` for one test."""
    module = types.ModuleType(f"research_core.watcher.{name}")
    module.run = run
    monkeypatch.setitem(sys.modules, module.__name__, module)


def test_steps_follow_the_design_order():
    assert STEP_ORDER == (
        "store_checks",
        "champion_sync",
        "waits",
        "recovery",
        "messages",
        "merge",
        "goals",
        "checks",
        "dispatch",
        "wakes",
        "heartbeat",
        "context_panel",
    )
    assert PAUSED_STEPS == {"store_checks", "heartbeat", "context_panel"}


def test_tick_runs_every_step_and_writes_the_heartbeat(tmp_path):
    ctx = make_ctx(tmp_path)
    report = tick(ctx)
    assert not report.paused
    assert list(report.steps) == list(STEP_ORDER)
    assert all(step.get("skipped") != "paused" for step in report.steps.values())
    assert report.steps["heartbeat"]["has_work"] is False
    assert ctx.meta("heartbeat") == now_iso(ctx.clock)
    assert ctx.meta("has_work") == "0" and report.has_work is False
    assert report.errors == []


def test_heartbeat_written_every_tick_and_logged_every_ten_minutes(tmp_path):
    ctx = make_ctx(tmp_path)
    tick(ctx)
    assert len(events_of(ctx.wstore, "watcher.heartbeat")) == 1
    advance(ctx, 60)
    tick(ctx)
    assert len(events_of(ctx.wstore, "watcher.heartbeat")) == 1
    assert ctx.meta("heartbeat") == now_iso(ctx.clock)
    advance(ctx, 9 * 60)
    report = tick(ctx)
    assert len(events_of(ctx.wstore, "watcher.heartbeat")) == 2
    assert report.steps["heartbeat"]["event"] is True


def test_pause_skips_background_steps(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "paused", True)
    emit(ctx.store, "task.asked", "T-1", {"question": "which seed?"})
    report = tick(ctx)
    assert report.paused
    for name in BACKGROUND_STEPS:
        assert report.steps[name] == {"skipped": "paused"}, name
    assert report.steps["store_checks"] == {"workspace": {}, "cow_fake": {}}
    assert "drain" in report.steps
    assert ctx.meta("heartbeat") == now_iso(ctx.clock)
    assert starts(ctx) == []  # the queued wake waits for the resume
    set_setting(ctx, "paused", False)
    report = tick(ctx)
    assert not report.paused
    assert "skipped" not in report.steps["wakes"]
    assert len(starts(ctx)) == 1 and "task.asked" in starts(ctx)[0]["text"]


def test_pause_drains_once_and_the_flag_clears_on_resume(tmp_path, monkeypatch):
    drains: list[int] = []
    fake_step(monkeypatch, "drain", lambda ctx: drains.append(1) or {"stopped": []})
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "paused", True)
    first = tick(ctx)
    assert first.steps["drain"] == {"stopped": []} and ctx.meta("paused_drained") == "1"
    second = tick(ctx)
    assert second.steps["drain"] == {"skipped": "already drained"}
    assert drains == [1]
    set_setting(ctx, "paused", False)
    tick(ctx)
    assert ctx.meta("paused_drained") == "0"
    set_setting(ctx, "paused", True)
    tick(ctx)
    assert drains == [1, 1]


def test_steps_tolerate_missing_and_failing_modules(tmp_path, monkeypatch):
    fake_step(monkeypatch, "goals", lambda ctx: 1 / 0)
    monkeypatch.setattr(
        tick_module, "STEP_ORDER", (*STEP_ORDER[:-2], "nonexistent_step", *STEP_ORDER[-2:])
    )
    ctx = make_ctx(tmp_path)
    report = tick(ctx)
    assert report.steps["nonexistent_step"] == {"skipped": "not built"}
    assert report.steps["goals"]["error"].startswith("ZeroDivisionError")
    assert [e for e in report.errors if e.startswith("goals: ZeroDivisionError")]
    assert ctx.meta("heartbeat") == now_iso(ctx.clock)  # the tick finished


def test_unmarked_write_detected(tmp_path):
    ctx = make_ctx(tmp_path)
    raw = sqlite3.connect(ctx.wstore.path)
    raw.execute("INSERT INTO settings(key, value) VALUES ('max_researchers', '9')")
    raw.commit()
    raw.close()
    report = tick(ctx)
    assert report.steps["store_checks"]["workspace"] == {"unmarked_writes": 1}
    [event] = events_of(ctx.wstore, "store.unmarked_write")
    assert event["subject"] == "workspace"
    assert event["payload"]["tables"] == ["settings"] and event["payload"]["count"] == 1
    assert event["payload"]["rows"][0]["tbl"] == "settings"
    assert any("outside the tools" in n["text"] for n in notices.unread(ctx.wstore))
    [start] = starts(ctx)
    assert "store.unmarked_write" in start["text"] and start["profile"] == "orchestrator"
    assert start["idempotency_key"] == f"event:workspace:{event['seq']}:1"
    tick(ctx)
    assert len(events_of(ctx.wstore, "store.unmarked_write")) == 1  # one report per batch
    assert len(starts(ctx)) == 1


def test_schema_rebuild_reported(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "submissions.mode", "ask")
    raw = sqlite3.connect(ctx.store.path)
    raw.execute("ALTER TABLE directives ADD COLUMN junk TEXT")
    raw.commit()
    raw.close()
    assert not ctx.store.schema_status().ok
    old_store = ctx.store
    report = tick(ctx)
    assert ctx.store is not old_store and ctx.store.schema_status().ok
    assert report.steps["store_checks"]["cow_fake"]["schema_rebuilt"] >= 1
    [event] = events_of(ctx.store, "store.schema_rebuilt")
    assert event["subject"] == "cow_fake"
    assert any("junk" in difference for difference in event["payload"]["differences"])
    assert event["payload"]["rebuild"]["dropped_columns"] == {"directives": ["junk"]}
    assert settings.get(ctx.store, "submissions.mode") == "ask"  # rows kept
    assert any("rebuilt" in n["text"] for n in notices.unread(ctx.wstore))
    [start] = starts(ctx)
    assert "store.schema_rebuilt" in start["text"]


def test_main_prints_one_json_line_and_exits_zero(tmp_path, capsys):
    profile = tmp_path / "profile"
    profile.mkdir()
    env = {
        "HERMES_HOME": str(profile),
        "IDE_WORKSPACE": str(tmp_path / "ws"),
        "IDE_PLATFORM": "fake",
    }
    assert tick_module.main([], env=env) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["wakeAgent"] is False and payload["paused"] is False
    assert payload["has_work"] is False and payload["errors"] == []
    assert list(payload["steps"]) == list(STEP_ORDER)


def test_main_needs_the_profile_home(tmp_path, capsys):
    assert tick_module.main([], env={"IDE_WORKSPACE": str(tmp_path)}) == 1
    assert "HERMES_HOME" in json.loads(capsys.readouterr().out.strip())["error"]


def test_main_rebuilds_a_mismatched_workspace_store_before_the_tick(tmp_path, capsys):
    profile = tmp_path / "profile"
    profile.mkdir()
    store_path = tmp_path / "ws" / ".state" / "store.db"
    Store.open(store_path, kind="workspace").close()
    raw = sqlite3.connect(store_path)
    raw.execute("ALTER TABLE directives ADD COLUMN junk TEXT")
    raw.commit()
    raw.close()
    env = {
        "HERMES_HOME": str(profile),
        "IDE_WORKSPACE": str(tmp_path / "ws"),
        "IDE_PLATFORM": "fake",
    }
    assert tick_module.main([], env=env) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["wakeAgent"] is False
    with Store.open(store_path, kind="workspace") as wstore:
        [event] = events_of(wstore, "store.schema_rebuilt")
        assert event["subject"] == "workspace"
        assert any("rebuilt" in n["text"] for n in notices.unread(wstore))
