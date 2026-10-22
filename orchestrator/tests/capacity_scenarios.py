"""Race native dispatchers with real worker processes at capacities one and three."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect
from hermes_cli.kanban_db_dispatch import dispatch_once


def main():
    capacity = int(sys.argv[1])
    home = Path(os.environ["HERMES_HOME"])
    for index in range(capacity):
        profile = home / f"profiles/researcher-{index + 1}"
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("{}\n")
    kb.create_board(slug="autoresearch", name="Research")
    with closing(connect(board="autoresearch")) as conn:
        for index in range(capacity * 2):
            kb.create_task(
                conn,
                title=f"Independent task {index}",
                assignee=f"researcher-{index % capacity + 1}",
            )
    workers = {}

    def spawn(task, workspace, board=None):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        workers[task.id] = process
        return process.pid

    def dispatch(_):
        with closing(connect(board="autoresearch")) as conn:
            return dispatch_once(
                conn,
                board="autoresearch",
                spawn_fn=spawn,
                max_in_progress=capacity,
                max_in_progress_per_profile=1,
                reconcile_orphans=False,
            )

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(dispatch, range(3)))
        assert len(workers) == capacity
        assert all(process.poll() is None for process in workers.values())
        dispatch(0)
        assert len(workers) == capacity
        cancelled = next(iter(workers))
        with closing(connect(board="autoresearch")) as conn:
            run_id = kb.get_task(conn, cancelled).current_run_id
            assert kb.archive_task(conn, cancelled)
            assert not kb.complete_task(
                conn, cancelled, summary="late completion", expected_run_id=run_id
            )
        workers[cancelled].wait(timeout=5)
        assert all(process.poll() is None for task, process in workers.items() if task != cancelled)
        dispatch(0)
        assert len(workers) == capacity + 1
        assert sum(process.poll() is None for process in workers.values()) == capacity
        print(
            json.dumps(
                {
                    "capacity": capacity,
                    "concurrent_dispatchers": 3,
                    "cancelled": cancelled,
                    "ok": True,
                }
            )
        )
    finally:
        for process in workers.values():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
