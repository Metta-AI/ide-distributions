"""Historical lifecycle triggers against real Hermes, SQLite and Git; no model or network."""

import json
import os
import runpy
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_boards import create_board

from orchestrator.board import Board, connection
from orchestrator.context import Context


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main():
    home = Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True)
    root = home.parent.parent
    seed = root / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.name", "Lifecycle test")
    git(seed, "config", "user.email", "test@example.invalid")
    (seed / "checkpoint").write_text("initial\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "seed")
    remote = root / "canonical.git"
    git(root, "clone", "--bare", str(seed), str(remote))
    for index in range(1, 4):
        profile = root / f"profiles/researcher-{index}"
        profile.mkdir()
        git(root, "clone", str(remote), str(profile / "workspace"))
    create_board("autoresearch")
    context = Context(home, dict(os.environ))
    board = Board(context)
    args = {
        "command_id": "event:1:create",
        "assignee": "researcher-1",
        "title": "Continue",
        "body": "Produce the requested change",
    }
    # Duplicate notifications and racing callers cannot create multiple tasks.
    with ThreadPoolExecutor(max_workers=3) as pool:
        cards = list(pool.map(lambda _: board.card_create(**args)["card"], range(3)))
    task_id = cards[0]["id"]
    assert {card["id"] for card in cards} == {task_id}
    assert task_id in cards[0]["branch_name"]
    with closing(connection()) as conn:
        assert kb.parent_ids(conn, task_id) == []
        task = kb.claim_task(conn, task_id)
        assert task is not None
        assert kb.block_task(conn, task_id, reason="Which change?", kind="needs_input")
    # #26: an integrated checkpoint is not a completion condition or a replacement requirement.
    clone = root / "profiles/researcher-1/workspace"
    git(clone, "push", "origin", cards[0]["branch_name"])
    assert git(clone, "rev-parse", cards[0]["branch_name"]) == git(seed, "rev-parse", "HEAD")
    answer = board.card_answer(task_id, "Continue with a new change after that checkpoint")
    assert answer["unblocked"]
    with closing(connection()) as conn:
        task = kb.claim_task(conn, task_id)
        assert task is not None
        git(clone, "switch", cards[0]["branch_name"])
        git(clone, "config", "user.name", "Lifecycle test")
        git(clone, "config", "user.email", "test@example.invalid")
        (clone / "checkpoint").write_text("continued after answer\n")
        git(clone, "add", "checkpoint")
        git(clone, "commit", "-m", "continue the same native task")
        git(clone, "push", "origin", cards[0]["branch_name"])
        result_commit = git(clone, "rev-parse", "HEAD")
        assert result_commit != git(seed, "rev-parse", "HEAD")
        result_run = task.current_run_id
        # #20: native assignment completion does not require canonical research metadata.
        assert kb.complete_task(
            conn,
            task_id,
            summary="Assignment finished; coordinator verifies artifacts",
            expected_run_id=task.current_run_id,
        )
    with patch("orchestrator.board.Research.call", return_value={"accepted": True}) as effect:
        assert board.workspace_integrate(task_id, result_run, result_commit) == {"accepted": True}
        assert effect.call_args.args[-2:] == ("--commit", result_commit)
    board.card_close(task_id, "archived")
    with patch("orchestrator.board.Research.call") as effect:
        with TestCase().assertRaises(ValueError):
            board.workspace_integrate(task_id, result_run, result_commit)
        effect.assert_not_called()
        with TestCase().assertRaises(ValueError):
            board.submit_requested_version(
                task_id, result_run, "V-cancelled", "strategy.md", "Submit it"
            )
        effect.assert_not_called()
    # Native replay also finds a terminal task; no semantic replacement or attempt key.
    assert board.card_create(**args)["card"]["id"] == task_id
    cancelled = board.card_create(**{**args, "command_id": "event:2:create"})["card"]
    board.card_close(cancelled["id"], "archived")
    assert (
        board.card_create(**{**args, "command_id": "event:2:create"})["card"]["status"]
        == "archived"
    )
    # A cancelled execution cannot admit another tool or deliver late native completion.
    guard = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "researcher/plugins/researcher_guard/__init__.py")
    )
    cancelled_running = board.card_create(**{**args, "command_id": "cancel-running"})["card"]
    with closing(connection()) as conn:
        running = kb.claim_task(conn, cancelled_running["id"])
        stale_run = running.current_run_id
    with patch.dict(
        os.environ,
        {
            "HERMES_KANBAN_TASK": running.id,
            "HERMES_KANBAN_RUN_ID": str(stale_run),
            "HERMES_KANBAN_DB": str(context.board_path),
        },
    ):
        assert guard["pre_tool_call"](tool_name="read_file", args={}) is None
        board.card_close(running.id, "archived")
        assert (
            guard["pre_tool_call"](tool_name="terminal", args={"command": "upload"})["action"]
            == "block"
        )
    with closing(connection()) as conn:
        assert not kb.complete_task(
            conn, running.id, summary="late result", expected_run_id=stale_run
        )
    # Independent assignments coexist, with native ownership of each run.
    for index in range(1, 4):
        card = board.card_create(
            **{**args, "command_id": f"parallel:{index}", "assignee": f"researcher-{index}"}
        )["card"]
        with closing(connection()) as conn:
            assert kb.claim_task(conn, card["id"]) is not None
            assert kb.claim_task(conn, card["id"]) is None
    print(json.dumps({"ok": True, "tasks": len(board.card_list()), "historical_cases": [20, 26]}))


if __name__ == "__main__":
    main()
