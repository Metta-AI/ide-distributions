"""Store.open, token-marked transactions, events, detection, the schema check and rebuild."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from research_core.store import db, migrate
from research_core.store.actors import Actor
from research_core.store.errors import SchemaMismatch
from research_core.store.events import Event

GOAL_COLUMNS = "title, end_state, reason, status, created, created_by, updated"


def insert_goal(tx, title: str = "Beat the champion") -> int:
    cur = tx.execute(
        f"INSERT INTO goals({GOAL_COLUMNS}) VALUES (?, ?, ?, 'active', ?, ?, ?)",
        (title, "Win rate above 60%", "the user asked", tx.at, str(tx.actor), tx.at),
    )
    return cur.lastrowid


def raw(path) -> sqlite3.Connection:
    """A plain connection: what an agent's direct ``sqlite3`` edit looks like."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


# --- open ---------------------------------------------------------------------------------


def test_open_creates_and_migrates(tmp_path):
    path = tmp_path / "deep" / ".state" / "store.db"
    store = db.Store.open(path, kind="coworld")
    try:
        assert path.is_file()
        assert store.one("PRAGMA user_version")[0] == 1
        assert store.one("PRAGMA journal_mode")[0] == "wal"
        assert store.one("PRAGMA foreign_keys")[0] == 1
        assert store.one("PRAGMA busy_timeout")[0] == 5000
        assert store.get_meta("schema_kind") == "coworld"
        assert store.get_meta("created")
        readme = path.parent / "README.md"
        assert readme.read_text() == db.STATE_README
        assert "store.db" in db.STATE_README and "Do not edit files here" in db.STATE_README
    finally:
        store.close()


def test_open_twice_is_idempotent(tmp_path):
    path = tmp_path / "store.db"
    db.Store.open(path, kind="coworld").close()
    (path.parent / "README.md").write_text("custom note")
    store = db.Store.open(path, kind="coworld")
    try:
        assert store.one("PRAGMA user_version")[0] == 1
        assert store.schema_status().ok
        assert (path.parent / "README.md").read_text() == "custom note"
    finally:
        store.close()


def test_open_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        db.Store.open(tmp_path / "store.db", kind="other")


def test_open_refuses_a_store_of_the_other_kind(tmp_path):
    path = tmp_path / "store.db"
    db.Store.open(path, kind="workspace").close()
    with pytest.raises(SchemaMismatch) as excinfo:
        db.Store.open(path, kind="coworld")
    assert any("schema_kind" in problem for problem in excinfo.value.problems)
    assert str(tmp_path) not in str(excinfo.value)


def test_store_is_a_context_manager(tmp_path):
    with db.Store.open(tmp_path / "store.db", kind="workspace") as store:
        assert store.kind == "workspace"
    with pytest.raises(sqlite3.ProgrammingError):
        store.q("SELECT 1")


# --- transactions, tokens and events ------------------------------------------------------


def test_tx_writes_event_and_stamps_change_log(coworld_store, harness):
    with coworld_store.tx(harness) as tx:
        goal_id = insert_goal(tx)
        seq = tx.event("goal.created", f"G-{goal_id}", {"title": "Beat the champion"})
        assert tx.token and len(tx.token) == 16
    assert seq == 1
    log = coworld_store.q("SELECT tbl, op, token FROM change_log ORDER BY id")
    assert [(r["tbl"], r["op"]) for r in log] == [("goals", "insert"), ("events", "insert")]
    assert {r["token"] for r in log} == {tx.token}
    events = coworld_store.events_after(0)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, Event)
    assert (event.seq, event.actor, event.kind, event.subject) == (
        1,
        "harness",
        "goal.created",
        "G-1",
    )
    assert event.payload == {"title": "Beat the champion"}
    assert event.at == tx.at == "2026-10-28T21:04:05.123456Z"
    assert coworld_store.unmarked_changes() == []


def test_tx_rolls_back_on_error(coworld_store, harness):
    with pytest.raises(RuntimeError, match="boom"):
        with coworld_store.tx(harness) as tx:
            insert_goal(tx)
            tx.event("goal.created", "G-1", {})
            raise RuntimeError("boom")
    assert coworld_store.one("SELECT COUNT(*) FROM goals")[0] == 0
    assert coworld_store.last_seq() == 0
    assert coworld_store.q("SELECT * FROM change_log") == []
    # The store is usable afterwards.
    with coworld_store.tx(harness) as tx:
        insert_goal(tx)
    assert coworld_store.one("SELECT COUNT(*) FROM goals")[0] == 1


def test_tx_cannot_nest_and_event_needs_an_open_tx(coworld_store, harness):
    with coworld_store.tx(harness) as tx:
        with pytest.raises(RuntimeError):
            with coworld_store.tx(harness):
                pass
    with pytest.raises(RuntimeError):
        tx.event("goal.created", "G-1", {})
    with pytest.raises(RuntimeError):
        tx.execute("SELECT 1")


def test_tx_records_the_actor_string(coworld_store, session_actor):
    user = Actor.parse("user:u_42")
    with coworld_store.tx(session_actor) as tx:
        tx.event("session.started", session_actor.id, {})
    with coworld_store.tx(user) as tx:
        tx.event("task.status", "T-1", {"status": "unclaimed"})
    actors = [e.actor for e in coworld_store.events_after(0)]
    assert actors == [f"session:{session_actor.id}", "user:u_42"]


def test_tx_takes_the_write_lock_at_begin(coworld_store, harness):
    """BEGIN IMMEDIATE: a second writer waits even before the first writes anything."""
    other = sqlite3.connect(coworld_store.path, isolation_level=None, timeout=0)
    try:
        with coworld_store.tx(harness):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()


def test_concurrent_writers_serialise(tmp_path):
    path = tmp_path / "store.db"
    db.Store.open(path, kind="coworld").close()
    errors: list[BaseException] = []

    def write(n: int) -> None:
        try:
            with db.Store.open(path, kind="coworld") as store:
                for _ in range(5):
                    with store.tx(Actor.harness()) as tx:
                        insert_goal(tx, f"goal {n}")
                        tx.event("goal.created", "G-?", {"n": n})
        except BaseException as exc:  # noqa: BLE001 - surfaced through the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with db.Store.open(path, kind="coworld") as store:
        assert store.one("SELECT COUNT(*) FROM goals")[0] == 20
        assert store.last_seq() == 20
        assert store.unmarked_changes() == []


def test_events_after_and_last_seq(coworld_store, harness):
    assert coworld_store.last_seq() == 0
    assert coworld_store.events_after(0) == []
    with coworld_store.tx(harness) as tx:
        for n in range(5):
            tx.event("notice", f"N-{n}", {"n": n})
    assert coworld_store.last_seq() == 5
    assert [e.seq for e in coworld_store.events_after(2)] == [3, 4, 5]
    assert [e.seq for e in coworld_store.events_after(0, limit=2)] == [1, 2]
    assert coworld_store.events_after(5) == []


def test_two_stores_independent_seq(coworld_store, workspace_store, harness):
    with coworld_store.tx(harness) as tx:
        assert tx.event("goal.created", "G-1", {}) == 1
    with workspace_store.tx(harness) as tx:
        assert tx.event("notice", "N-1", {}) == 1
    assert coworld_store.last_seq() == 1
    assert workspace_store.last_seq() == 1


def test_event_payload_must_be_a_dict(coworld_store, harness):
    with pytest.raises(TypeError):
        with coworld_store.tx(harness) as tx:
            tx.event("notice", "N-1", ["not", "a", "dict"])


def test_meta_helpers_do_not_touch_change_log(coworld_store):
    coworld_store.set_meta("heartbeat", "2026-10-28T21:05:00.000000Z")
    assert coworld_store.get_meta("heartbeat") == "2026-10-28T21:05:00.000000Z"
    assert coworld_store.get_meta("missing") is None
    assert coworld_store.q("SELECT * FROM change_log") == []


# --- detection ----------------------------------------------------------------------------


def test_unmarked_write_detected(coworld_store, harness):
    other = raw(coworld_store.path)
    try:
        other.execute(
            f"INSERT INTO goals({GOAL_COLUMNS}) VALUES ('x', 'y', 'z', 'active', 't', 'me', 't')"
        )
    finally:
        other.close()
    unmarked = coworld_store.unmarked_changes()
    assert len(unmarked) == 1
    assert unmarked[0]["tbl"] == "goals"
    assert unmarked[0]["op"] == "insert"
    assert unmarked[0]["row_id"] == 1
    # A later library transaction stamps only its own rows; the stray one stays visible.
    with coworld_store.tx(harness) as tx:
        insert_goal(tx)
    assert [row["row_id"] for row in coworld_store.unmarked_changes()] == [1]
    coworld_store.acknowledge_unmarked([unmarked[0]["id"]], "reported")
    assert coworld_store.unmarked_changes() == []
    token = coworld_store.one("SELECT token FROM change_log WHERE id = ?", (unmarked[0]["id"],))
    assert token[0] == "ack:reported"


def test_unmarked_update_and_delete_detected(coworld_store, harness):
    with coworld_store.tx(harness) as tx:
        insert_goal(tx)
    other = raw(coworld_store.path)
    try:
        other.execute("UPDATE goals SET title = 'edited' WHERE id = 1")
        other.execute("DELETE FROM goals WHERE id = 1")
    finally:
        other.close()
    ops = [(row["tbl"], row["op"]) for row in coworld_store.unmarked_changes()]
    assert ops == [("goals", "update"), ("goals", "delete")]


def test_unmarked_event_insert_detected(coworld_store):
    other = raw(coworld_store.path)
    try:
        other.execute(
            "INSERT INTO events(at, actor, kind, subject) VALUES ('t', 'me', 'notice', 'N-1')"
        )
    finally:
        other.close()
    assert [row["tbl"] for row in coworld_store.unmarked_changes()] == ["events"]


def test_events_append_only(coworld_store, harness):
    with coworld_store.tx(harness) as tx:
        tx.event("notice", "N-1", {})
    other = raw(coworld_store.path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            other.execute("UPDATE events SET kind = 'x' WHERE seq = 1")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            other.execute("DELETE FROM events WHERE seq = 1")
    finally:
        other.close()
    assert coworld_store.last_seq() == 1


# --- schema check and rebuild -------------------------------------------------------------


def test_schema_status_ok(coworld_store, workspace_store):
    for store in (coworld_store, workspace_store):
        status = store.schema_status()
        assert status.ok
        assert status.diff == []
        assert status.user_version == status.expected_version == 1


def test_schema_mismatch_refused(tmp_path):
    path = tmp_path / "store.db"
    db.Store.open(path, kind="coworld").close()
    other = raw(path)
    try:
        other.execute("ALTER TABLE goals ADD COLUMN x TEXT")
    finally:
        other.close()
    with pytest.raises(SchemaMismatch) as excinfo:
        db.Store.open(path, kind="coworld")
    problems = excinfo.value.problems
    assert len(problems) == 1
    assert "goals" in problems[0]
    assert str(tmp_path) not in str(excinfo.value)
    assert "goals" in str(excinfo.value)


def test_schema_mismatch_lists_every_difference(tmp_path):
    path = tmp_path / "store.db"
    db.Store.open(path, kind="coworld").close()
    other = raw(path)
    try:
        other.execute("ALTER TABLE goals ADD COLUMN x TEXT")
        other.execute("DROP TRIGGER events_append_only_update")
        other.execute("CREATE TABLE scratch(a)")
        other.execute("CREATE INDEX scratch_a ON scratch(a)")
    finally:
        other.close()
    with pytest.raises(SchemaMismatch) as excinfo:
        db.Store.open(path, kind="coworld")
    text = "\n".join(excinfo.value.problems)
    assert len(excinfo.value.problems) == 4
    assert "table goals" in text
    assert "trigger events_append_only_update" in text
    assert "table scratch" in text
    assert "index scratch_a" in text


def test_rebuild_keeps_rows(tmp_path, harness):
    path = tmp_path / "cow" / ".state" / "store.db"
    with db.Store.open(path, kind="coworld") as store:
        with store.tx(harness) as tx:
            insert_goal(tx, "one")
            tx.event("goal.created", "G-1", {"n": 1})
        with store.tx(harness) as tx:
            insert_goal(tx, "two")
            tx.event("goal.created", "G-2", {"n": 2})
            tx.execute(
                "INSERT INTO claims(summary, body, tags, status, created, created_by, updated)"
                " VALUES ('a claim', 'body text', '[\"G-1\"]', 'active', ?, ?, ?)",
                (tx.at, str(tx.actor), tx.at),
            )
        store.set_meta("heartbeat", "beat")
    other = raw(path)
    try:
        other.execute("ALTER TABLE goals ADD COLUMN x TEXT")
        other.execute("UPDATE goals SET x = 'stray' WHERE id = 2")
        other.execute("INSERT INTO settings(key, value) VALUES ('paused', 'true')")
    finally:
        other.close()
    with pytest.raises(SchemaMismatch):
        db.Store.open(path, kind="coworld")

    report = migrate.rebuild_keeping_rows(path, "coworld")

    assert report.tables["goals"] == 2
    assert report.tables["events"] == 2
    assert report.tables["claims"] == 1
    assert report.tables["settings"] == 1
    assert report.dropped_columns == {"goals": ["x"]}
    assert report.user_version == 1
    as_dict = report.to_dict()
    assert as_dict["tables"]["goals"] == 2 and as_dict["dropped_columns"] == {"goals": ["x"]}
    assert not path.with_name(path.name + ".rebuild").exists()
    with db.Store.open(path, kind="coworld") as store:
        assert store.schema_status().ok
        assert store.one("PRAGMA user_version")[0] == 1
        assert [r["title"] for r in store.q("SELECT title FROM goals ORDER BY id")] == [
            "one",
            "two",
        ]
        assert [e.seq for e in store.events_after(0)] == [1, 2]
        assert store.events_after(0)[1].payload == {"n": 2}
        assert store.last_seq() == 2
        assert store.get_meta("heartbeat") == "beat"
        assert store.get_meta("schema_kind") == "coworld"
        assert store.one("SELECT value FROM settings WHERE key = 'paused'")[0] == "true"
        # The FTS index was rebuilt by the sync triggers.
        hit = store.one("SELECT rowid FROM claims_fts WHERE claims_fts MATCH 'claim'")
        assert hit[0] == 1
        # The stray settings insert stays visible as an unmarked write; the copy itself is marked.
        unmarked = store.unmarked_changes()
        assert [(row["tbl"], row["op"]) for row in unmarked] == [
            ("goals", "update"),
            ("settings", "insert"),
        ]
        tokens = {
            r["token"] for r in store.q("SELECT token FROM change_log WHERE token IS NOT NULL")
        }
        assert any(t.startswith("rebuild:") for t in tokens)
        # New events continue the sequence.
        with store.tx(harness) as tx:
            assert tx.event("notice", "N-1", {}) == 3


def test_rebuild_reports_dropped_tables(tmp_path):
    path = tmp_path / "store.db"
    db.Store.open(path, kind="workspace").close()
    other = raw(path)
    try:
        other.execute("CREATE TABLE extra(a, b)")
        other.execute("INSERT INTO extra VALUES (1, 2)")
    finally:
        other.close()
    report = migrate.rebuild_keeping_rows(path, "workspace")
    assert report.dropped_tables == ["extra"]
    assert "extra" not in report.tables
    with db.Store.open(path, kind="workspace") as store:
        assert store.schema_status().ok


def test_error_messages_never_name_the_store_path(tmp_path):
    path = tmp_path / "secret-folder" / "store.db"
    db.Store.open(path, kind="coworld").close()
    other = raw(path)
    try:
        other.execute("ALTER TABLE goals ADD COLUMN x TEXT")
    finally:
        other.close()
    with pytest.raises(SchemaMismatch) as excinfo:
        db.Store.open(path, kind="coworld")
    assert "secret-folder" not in str(excinfo.value)
    assert "secret-folder" not in repr(excinfo.value)
