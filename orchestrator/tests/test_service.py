import json
import sqlite3

from orchestrator.service import Service


def test_reconcile_distinguishes_queued_base_from_integrated_work(context, board_file):
    body = "\n\nCard context:\n```json\n" + json.dumps({"base_sha": "base"}) + "\n```\n"
    with sqlite3.connect(board_file) as conn:
        conn.execute("UPDATE tasks SET status='ready', body=?", (body,))

    class Shared:
        def __init__(self):
            self.merged = {"merged": True, "card_sha": "base", "main_sha": "main"}

        def call(self, *args):
            if args == ("records", "summary"):
                return {"champion": None}
            if args == ("promotion", "status"):
                return {"conflict": False, "in_flight": None, "champion": None}
            assert args == ("workspace", "merged", "--branch", "card/test")
            return self.merged

    service = Service(context)
    service.research = Shared()
    assert service.board_reconcile()["branches"] == []
    service.research.merged = {"merged": True, "card_sha": "work", "main_sha": "main"}
    assert service.board_reconcile()["branches"][0]["card"] == "card-1"
    with sqlite3.connect(board_file) as conn:
        conn.execute("UPDATE tasks SET status='done'")
    service.research.merged = {"merged": False, "card_sha": "work", "main_sha": "main"}
    assert service.board_reconcile()["branches"][0]["merged"] is False
