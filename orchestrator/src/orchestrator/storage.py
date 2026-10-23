"""Coordinator conversation binding and read-only native task queries."""

import sqlite3
from contextlib import closing


def open_storage(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS coordinator (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def snapshot(context):
    """Read current native waits without importing Hermes in the cron subprocess."""
    if not context.board_path.exists():
        return {"awaiting": []}
    with closing(sqlite3.connect(f"{context.board_path.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        # ``game``: the card's game from its creation command (NULL: the default game).
        runs = [
            dict(row)
            for row in conn.execute(
                "SELECT r.*, (SELECT json_extract(e.payload, '$.game') FROM task_events e "
                "WHERE e.task_id=r.task_id AND e.kind='creation_command' ORDER BY e.id LIMIT 1) "
                "AS game FROM task_runs r JOIN tasks t ON t.id=r.task_id "
                "WHERE t.status != 'archived' AND r.id = "
                "(SELECT MAX(id) FROM task_runs WHERE task_id=r.task_id) "
                "AND r.ended_at IS NOT NULL AND r.metadata IS NOT NULL"
            )
        ]
    return {"awaiting": runs}
