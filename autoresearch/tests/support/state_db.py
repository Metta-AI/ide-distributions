"""A minimal Hermes ``state.db`` for spend tests: the ``sessions`` columns ``runtime.spend``
reads (Hermes v0.21.5 ``hermes_state_common.py`` sessions DDL), and plugin session events."""

import json
import sqlite3
import time
from pathlib import Path

SESSIONS_DDL = """CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, parent_session_id TEXT,
    started_at REAL NOT NULL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT
)"""


def add_session(
    hermes_home: Path,
    session_id: str,
    usd: float | None = None,
    *,
    parent: str | None = None,
    started_at: float | None = None,
    status: str | None = "estimated",
    tokens: tuple[int, int, int, int] = (0, 0, 0, 0),
    actual: float | None = None,
) -> None:
    """Insert one session (``tokens``: input, output, cache read, cache write)."""
    connection = sqlite3.connect(Path(hermes_home) / "state.db")
    try:
        connection.execute(SESSIONS_DDL)
        connection.execute(
            "INSERT INTO sessions (id, source, parent_session_id, started_at, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, estimated_cost_usd, "
            "actual_cost_usd, cost_status) VALUES (?, 'kanban', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, parent, time.time() if started_at is None else started_at, *tokens,
             usd, actual, status),
        )
        connection.commit()
    finally:
        connection.close()


def record_session_start(canonical: Path, task_id: str, run_id: str, session_id: str) -> None:
    """What the plugin's first-turn hook appends (``plugin.adapters.pre_llm_call``)."""
    path = Path(canonical) / ".supervisor" / "runtime" / "kanban_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.time(), "event": "session_start", "task": task_id, "run": run_id,
             "session_id": session_id}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
