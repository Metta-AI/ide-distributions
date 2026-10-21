"""Transport receipts and acknowledgements; native Hermes owns task state."""

import sqlite3
from contextlib import closing

TABLES = ("task_events", "task_comments", "task_runs")
# Hermes gateway/kanban_watchers_notifier.py:_WAKE_KINDS. The gate runs without Hermes.
WAKE_KINDS = {
    "completed",
    "gave_up",
    "crashed",
    "timed_out",
    "blocked",
    "review_requested",
    "changes_requested",
    "block_loop_detected",
}


def prepare(conn):
    # Reads do not wait behind a creation command's Git preparation transaction.
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {"handled", "commands", "coordinator", "wakes"} <= tables:
        return conn
    conn.execute("CREATE TABLE IF NOT EXISTS wakes (id TEXT PRIMARY KEY, retry_until REAL NOT NULL, run_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS coordinator (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        'CREATE TABLE IF NOT EXISTS handled ("table" TEXT NOT NULL, '
        'rowid INTEGER NOT NULL, PRIMARY KEY ("table", rowid))'
    )
    conn.commit()
    return conn


def plugin_storage():
    from plugins.plugin_storage import plugin_db

    conn = plugin_db("orchestrator")
    conn.execute("PRAGMA busy_timeout=60000")
    return prepare(conn)


def open_storage(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return prepare(sqlite3.connect(path, timeout=60))


def ack(conn, ids):
    rows = []
    for event in ids:
        if event["table"] not in TABLES:
            raise ValueError("event table must be task_events, task_comments or task_runs")
        rows.append((event["table"], int(event["id"])))
    with conn:
        conn.executemany('INSERT OR IGNORE INTO handled ("table", rowid) VALUES (?, ?)', rows)
    return {"acknowledged": len(rows)}


def snapshot(context):
    """Read native tables without importing Hermes in the profile venv."""
    with closing(open_storage(context.storage_path)) as store:
        handled = set(store.execute('SELECT "table", rowid FROM handled'))
    if not context.board_path.exists():
        return {"events": [], "awaiting": [], "cards": []}
    with closing(sqlite3.connect(f"{context.board_path.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        events = []
        for table in ("task_events", "task_comments"):
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):
                if table == "task_events" and row["kind"] not in WAKE_KINDS:
                    continue
                if table == "task_comments" and row["author"] == "orchestrator":
                    continue
                if (table, row["id"]) not in handled:
                    events.append({"table": table, **dict(row)})
        runs = [
            dict(row)
            for row in conn.execute(
                "SELECT r.* FROM task_runs r JOIN tasks t ON t.id=r.task_id "
                "WHERE t.status != 'archived' AND r.id = "
                "(SELECT MAX(id) FROM task_runs WHERE task_id=r.task_id) "
                "AND r.ended_at IS NOT NULL AND r.metadata IS NOT NULL"
            )
            if ("task_runs", row["id"]) not in handled
        ]
        cards = [dict(row) for row in conn.execute("SELECT * FROM tasks WHERE status!='archived'")]
    return {"events": events, "awaiting": runs, "cards": cards}
