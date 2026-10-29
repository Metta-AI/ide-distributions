"""Coordinator conversation binding and read-only native task queries."""

import json
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
    handled = handled_waits(context)
    for run in runs:
        metadata = json.loads(run["metadata"])
        awaiting = metadata.get("awaiting") or {}
        for wait, kind in WAIT_KINDS.items():
            if f"task_runs:{run['id']}:{kind}" in handled:
                awaiting.pop(wait, None)
        metadata["awaiting"] = awaiting
        run["metadata"] = json.dumps(metadata)
    return {"awaiting": runs}


def card_sessions(context):
    """Each card's origin: the coordinator session that created it (native ``tasks.session_id``)."""
    if not context.board_path.exists():
        return {}
    with closing(sqlite3.connect(f"{context.board_path.as_uri()}?mode=ro", uri=True)) as conn:
        return dict(conn.execute("SELECT id, session_id FROM tasks WHERE session_id IS NOT NULL"))


# Coordinator keys per champion submission (``CS-`` id): the session that made it, and whether
# its first league match has woken the coordinator. Plain keys in the one key/value table.
SUBMISSION_SESSION = "submission_session:"
LEAGUE_RESULT_SEEN = "league_result_seen:"


def remember_submission_session(context, submission_id, session):
    with closing(open_storage(context.storage_path)) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO coordinator(key,value) VALUES (?, ?)",
            (SUBMISSION_SESSION + submission_id, session),
        )


def submission_sessions(context):
    return {
        key.removeprefix(SUBMISSION_SESSION): value
        for key, value in _keys(context, SUBMISSION_SESSION)
    }


def league_results_seen(context):
    return {key.removeprefix(LEAGUE_RESULT_SEEN) for key, _ in _keys(context, LEAGUE_RESULT_SEEN)}


def mark_league_result_seen(context, submission_id):
    with closing(open_storage(context.storage_path)) as conn, conn:
        conn.execute(
            "INSERT OR IGNORE INTO coordinator(key,value) VALUES (?, 'woken')",
            (LEAGUE_RESULT_SEEN + submission_id,),
        )


def _keys(context, prefix):
    if not context.storage_path.exists():
        return []
    with closing(sqlite3.connect(f"{context.storage_path.as_uri()}?mode=ro", uri=True)) as conn:
        return conn.execute(
            "SELECT key, value FROM coordinator WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
        ).fetchall()


# A wait keeps its native run metadata. This relation names the native input handling it;
# only that input's successful completion retires the wait from the read projection.
WAIT_DELIVERY = "wait_delivery:"
WAIT_KINDS = {"xp": "xp_terminal", "credits": "credits_available", "permit": "bad_handoff"}


def remember_wait_delivery(context, events, identity):
    waits = [
        (WAIT_DELIVERY + event["source_event"], identity)
        for event in events
        if event.get("table") == "task_runs" and event["kind"] in WAIT_KINDS.values()
    ]
    with closing(open_storage(context.storage_path)) as conn, conn:
        conn.executemany("INSERT OR IGNORE INTO coordinator(key,value) VALUES (?, ?)", waits)


def wait_deliveries(context):
    return {
        key.removeprefix(WAIT_DELIVERY): identity for key, identity in _keys(context, WAIT_DELIVERY)
    }


def handled_waits(context):
    deliveries = wait_deliveries(context)
    if not deliveries:
        return set()
    # Same read-only native-state boundary as the Kanban snapshot above. Do not infer
    # completion from an absent pending response: fresh, running and failed inputs differ.
    database = context.home / "state.db"
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as conn:
        return {
            source_event
            for source_event, identity in deliveries.items()
            if conn.execute(
                "SELECT 1 FROM coordinator_inbox WHERE identity = ? "
                "AND json_extract(result, '$.completed') = 1",
                (identity,),
            ).fetchone()
        }
