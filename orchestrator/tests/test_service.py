import json
import sqlite3
from contextlib import closing

from orchestrator.service import Service
from orchestrator.storage import ack, open_storage


def test_reconcile_distinguishes_queued_base_from_integrated_work(context, board_file):
    body = "\n\nCard context:\n```json\n" + json.dumps({"base_sha": "base"}) + "\n```\n"
    with sqlite3.connect(board_file) as conn:
        conn.execute("ALTER TABLE tasks ADD COLUMN last_failure_error TEXT")
        conn.execute("ALTER TABLE tasks ADD COLUMN consecutive_failures INTEGER DEFAULT 0")
        conn.execute("UPDATE tasks SET status='ready', body=?", (body,))

    class Shared:
        def __init__(self):
            self.merged = {"merged": True, "card_sha": "base", "main_sha": "main"}
            self.fetches = 0

        def call(self, *args):
            if args == ("records", "summary"):
                return {"champion": None}
            if args == ("promotion", "status"):
                return {"conflict": False, "in_flight": None, "champion": None}
            assert args == ("workspace", "merged", "--branch", "card/test")
            self.fetches += 1
            return self.merged

    service = Service(context)
    service.research = Shared()
    assert service.board_reconcile()["branches"] == []
    assert service.research.fetches == 0
    with sqlite3.connect(board_file) as conn:
        conn.execute("INSERT INTO task_events VALUES (1, 'card-1', 'crashed')")
        # An old done branch and a card with only bookkeeping must never be fetched.
        conn.execute(
            "INSERT INTO tasks (id,status,branch_name,body) VALUES ('old', 'done', 'card/old', '')"
        )
        conn.execute(
            "INSERT INTO tasks (id,status,branch_name,body) VALUES ('other', 'blocked', 'card/other', '')"
        )
        conn.execute("INSERT INTO task_events VALUES (2, 'other', 'edited')")
    with sqlite3.connect(board_file) as conn:
        conn.execute(
            "UPDATE tasks SET status='blocked', last_failure_error='Unknown skill(s): missing', "
            "consecutive_failures=2 WHERE id='card-1'"
        )
    reconciliation = service.board_reconcile()
    assert reconciliation["branches"] == []
    assert len(reconciliation["cards"]) == 1
    failure = reconciliation["cards"][0]
    assert failure["status"] == "blocked"
    assert failure["last_failure_error"] == "Unknown skill(s): missing"
    assert failure["consecutive_failures"] == 2
    assert service.research.fetches == 1
    service.research.merged = {"merged": True, "card_sha": "work", "main_sha": "main"}
    assert service.board_reconcile()["branches"][0]["card"] == "card-1"
    with sqlite3.connect(board_file) as conn:
        conn.execute("UPDATE tasks SET status='done'")
    service.research.merged = {"merged": False, "card_sha": "work", "main_sha": "main"}
    result = service.board_reconcile()
    assert result["branches"][0]["merged"] is False
    assert service.research.fetches == 3
    with closing(open_storage(context.storage_path)) as store:
        ack(store, result["events"])
    assert service.board_reconcile()["branches"] == []
    assert service.research.fetches == 3
