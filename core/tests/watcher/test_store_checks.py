"""Steps 1 and 2: the pause check, unmarked-write detection and schema rebuilds (design B.10)."""

from __future__ import annotations

import sqlite3

from research_core.store import notices, settings
from research_core.store.db import Store
from research_core.store.errors import SchemaMismatch
from research_core.watcher import store_checks
from tests.watcher._support import USER, events_of, make_ctx, set_setting


def test_pause_state_reads_the_workspace_setting(tmp_path):
    ctx = make_ctx(tmp_path)
    assert store_checks.pause_state(ctx) is False
    set_setting(ctx, "paused", True)
    assert store_checks.pause_state(ctx) is True


def test_labelled_stores_names_the_workspace_and_the_coworld(tmp_path):
    ctx = make_ctx(tmp_path)
    assert [label for label, _ in store_checks.labelled_stores(ctx)] == ["workspace", "cow_fake"]
    ctx.store = None
    assert [label for label, _ in store_checks.labelled_stores(ctx)] == ["workspace"]


def test_nothing_to_report_on_a_clean_store(tmp_path):
    ctx = make_ctx(tmp_path)
    settings.update(ctx.store, USER, "submissions.mode", "ask", cites="msg-1")  # a marked write
    assert store_checks.run(ctx) == {"workspace": {}, "cow_fake": {}}
    assert events_of(ctx.store, "store.unmarked_write") == []


def test_unmarked_writes_in_the_coworld_store_are_reported_once(tmp_path):
    ctx = make_ctx(tmp_path)
    raw = sqlite3.connect(ctx.store.path)
    raw.execute(
        "INSERT INTO directives(text, scope, source, status)"
        " VALUES ('be bold', 'coworld', 'user', 'active')"
    )
    raw.execute("UPDATE directives SET text = 'be bolder' WHERE text = 'be bold'")
    raw.commit()
    raw.close()
    report = store_checks.run(ctx)
    assert report == {"workspace": {}, "cow_fake": {"unmarked_writes": 2}}
    [event] = events_of(ctx.store, "store.unmarked_write")
    assert event["subject"] == "cow_fake" and event["payload"]["store"] == "cow_fake"
    assert event["payload"]["count"] == 2 and event["payload"]["tables"] == ["directives"]
    assert [row["op"] for row in event["payload"]["rows"]] == ["insert", "update"]
    [notice] = notices.unread(ctx.wstore)
    assert "Coworld cow_fake" in notice["text"] and "directives" in notice["text"]
    assert ctx.store.unmarked_changes() == []
    assert store_checks.run(ctx) == {"workspace": {}, "cow_fake": {}}
    assert len(events_of(ctx.store, "store.unmarked_write")) == 1


def test_schema_mismatch_rebuilds_the_workspace_store_keeping_rows(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "max_researchers", 3)
    raw = sqlite3.connect(ctx.wstore.path)
    raw.execute("CREATE TABLE scratch (id INTEGER PRIMARY KEY, note TEXT)")
    raw.execute("INSERT INTO scratch(note) VALUES ('left by a shell command')")
    raw.commit()
    raw.close()
    old = ctx.wstore
    report = store_checks.run(ctx)
    assert report["workspace"]["schema_rebuilt"] >= 1
    assert ctx.wstore is not old and ctx.wstore.schema_status().ok
    assert settings.get(ctx.wstore, "max_researchers") == 3
    [event] = events_of(ctx.wstore, "store.schema_rebuilt")
    assert event["subject"] == "workspace"
    assert "unexpected table scratch" in event["payload"]["differences"]
    assert event["payload"]["rebuild"]["dropped_tables"] == ["scratch"]
    [notice] = notices.unread(ctx.wstore)
    assert "rebuilt" in notice["text"] and "scratch" in notice["text"]
    assert ctx.wstore.one("SELECT 1 FROM sqlite_master WHERE name = 'scratch'") is None


def test_repair_unopenable_rebuilds_both_stores_before_a_tick(tmp_path):
    ctx = make_ctx(tmp_path)
    set_setting(ctx, "submissions.mode", "ask")
    wstore_path, store_path = ctx.wstore.path, ctx.store.path
    ctx.wstore.close()
    ctx.store.close()
    for path in (wstore_path, store_path):
        raw = sqlite3.connect(path)
        raw.execute("ALTER TABLE directives ADD COLUMN junk TEXT")
        raw.commit()
        raw.close()
    try:
        Store.open(wstore_path, kind="workspace")
    except SchemaMismatch:
        pass
    else:
        raise AssertionError("the altered store should not open")

    repaired = store_checks.repair_unopenable({"IDE_WORKSPACE": str(ctx.ws.root)})
    assert [entry["store"] for entry in repaired] == ["workspace", "cow_fake"]
    assert all(entry["dropped_columns"] == {"directives": ["junk"]} for entry in repaired)
    with Store.open(wstore_path, kind="workspace") as wstore:
        assert len(events_of(wstore, "store.schema_rebuilt")) == 1
        texts = [n["text"] for n in notices.unread(wstore)]
        assert len(texts) == 2 and any("Coworld cow_fake" in t for t in texts)
    with Store.open(store_path, kind="coworld") as store:
        assert len(events_of(store, "store.schema_rebuilt")) == 1
        assert settings.get(store, "submissions.mode") == "ask"
    assert store_checks.repair_unopenable({"IDE_WORKSPACE": str(ctx.ws.root)}) == []
