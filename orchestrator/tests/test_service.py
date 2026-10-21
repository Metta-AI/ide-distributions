import sqlite3
from contextlib import closing

from orchestrator.service import Service
from orchestrator.storage import ack, open_storage


def test_reconciliation_reports_native_state_without_inventing_git_lifecycle(context, board_file):
    class Shared:
        def call(self, *args):
            if args == ("records", "summary"):
                return {"champion": None}
            assert args == ("promotion", "status")
            return {"conflict": False, "in_flight": None, "champion": None}

    service = Service(context)
    service.research = Shared()
    with sqlite3.connect(board_file) as conn:
        conn.execute("UPDATE tasks SET status='blocked'")
        conn.execute("INSERT INTO task_events VALUES (1, 'card-1', 'blocked')")
    result = service.board_reconcile()
    assert result["cards"][0]["status"] == "blocked"
    assert len(result["events"]) == 1
    with closing(open_storage(context.storage_path)) as store:
        ack(store, result["events"])
    assert service.board_reconcile()["events"] == []
