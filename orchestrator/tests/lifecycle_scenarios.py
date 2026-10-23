"""Historical lifecycle triggers against real Hermes, SQLite and Git; no model or network."""

import json
import os
import runpy
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_boards import create_board
from hermes_cli.kanban_db_workspace import resolve_workspace, set_workspace_path

from orchestrator.board import Board, connection
from orchestrator.context import Context


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def game_cards(root, home, args):
    """A card names its game: the assignee's own CLI prepares its clone of that game, the
    card's worktree comes from that clone, and card effects run in that game's workspace."""
    seed = root / "seed-newgame"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.name", "Lifecycle test")
    git(seed, "config", "user.email", "test@example.invalid")
    (seed / "game-checkpoint").write_text("newgame\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "seed newgame")
    git(root, "clone", "--bare", str(seed), str(root / "canonical-newgame.git"))
    (root / "games/newgame").mkdir(parents=True)
    (root / "games/newgame/GAME.md").write_text("---\nname: newgame\n---\n")  # installed
    researcher = root / "profiles/researcher-1"
    shim = researcher / "bin/research"
    shim.parent.mkdir()
    shim.write_text(
        "#!/bin/sh\nset -e\n"
        '[ "$*" = "workspace bootstrap --game newgame" ]\n'
        '[ -d "$HERMES_HOME/workspace-newgame" ] || '
        f'git clone -q "{root / "canonical-newgame.git"}" "$HERMES_HOME/workspace-newgame"\n'
        'echo \'{"ok": true, "data": {"actions": ["cloned"]}}\'\n'
    )
    shim.chmod(0o755)
    env = {
        **os.environ,
        "RESEARCH_GAME": "default-game",
        "RESEARCH_CANONICAL": str(root / "canonical.git"),
        "RESEARCH_WORKSPACE": str(home / "workspace"),
    }
    board = Board(Context(home, env))
    game_args = {**args, "command_id": "game:newgame:1", "game": "newgame"}
    card = board.card_create(**game_args)["card"]
    clone = researcher / "workspace-newgame"
    assert git(clone, "rev-parse", card["branch_name"]) == git(seed, "rev-parse", "HEAD")
    assert board._card_game(card["id"]) == "newgame"
    assert board.card_create(**game_args)["card"]["id"] == card["id"]
    with TestCase().assertRaisesRegex(ValueError, "different arguments"):
        board.card_create(**{**game_args, "game": None})
    with TestCase().assertRaisesRegex(Exception, "not found"):
        board.card_create(**{**game_args, "command_id": "game:absent", "game": "absent"})
    # An omitted game stays neutral, even when a caller still carries an old profile default.
    default_card = board.card_create(**{**args, "command_id": "game:default"})["card"]
    assert board._card_game(default_card["id"]) is None
    legacy = Board(Context(home, dict(os.environ))).card_create(
        **{**args, "command_id": "game:legacy"}
    )["card"]
    assert board._card_game(legacy["id"]) is None
    assert board.card_create(**{**args, "command_id": "game:legacy"})["card"]["id"] == legacy["id"]
    with closing(connection()) as conn:
        task = kb.claim_task(conn, card["id"])
        worktree = resolve_workspace(task, board="autoresearch")
        assert worktree.resolve().is_relative_to(clone.resolve())
        assert kb.complete_task(conn, task.id, summary="done", expected_run_id=task.current_run_id)
    calls = []

    def record(research, *arguments):
        calls.append((research.context.env["RESEARCH_GAME"], arguments[:2]))
        return {"accepted": True}

    with patch("orchestrator.board.Research.call", autospec=True, side_effect=record):
        board.workspace_integrate(card["id"], task.current_run_id, "c" * 40)
    # The orchestrator's own clone of the game is made first, then the effect runs there.
    assert calls == [
        ("newgame", ("workspace", "bootstrap")),
        ("newgame", ("workspace", "integrate")),
    ], calls


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
    native_create = kb.create_task

    def crash_after_creation(*args, **kwargs):
        native_create(*args, **kwargs)
        raise RuntimeError("crash after native creation")

    interrupted = {**args, "command_id": "crash:created"}
    with (
        patch("hermes_cli.kanban_db.create_task", side_effect=crash_after_creation),
        TestCase().assertRaisesRegex(RuntimeError, "crash after native creation"),
    ):
        board.card_create(**interrupted)
    source_run = "run_" + "a" * 32
    with patch("tools.approval_context.get_current_session_key", return_value=source_run):
        recovered = board.card_create(**interrupted)["card"]
    with patch("tools.approval_context.get_current_session_key", return_value="run_" + "b" * 32):
        assert board.card_create(**interrupted)["card"]["id"] == recovered["id"]
    with closing(connection()) as conn:
        assert len(kb.list_tasks(conn)) == 1
        saved = kb.get_task(conn, recovered["id"])
        assert saved.status == "ready"
        marker = "\n\nCard context:\n```json\n"
        original_body, metadata = saved.body.rsplit(marker, 1)
        assert original_body == interrupted["body"]
        assert json.loads(metadata.split("\n```", 1)[0]) == {"source_run_id": source_run}
    board.card_close(recovered["id"], "archived")
    triaged = board.card_create(**{**args, "command_id": "triage:resume"})["card"]
    for _ in range(kb.BLOCK_RECURRENCE_LIMIT):
        with closing(connection()) as conn:
            assert kb.claim_task(conn, triaged["id"]) is not None
            assert kb.block_task(conn, triaged["id"], reason="Tool unavailable", kind="needs_input")
            status = kb.get_task(conn, triaged["id"]).status
        if status != "triage":
            assert board.card_answer(triaged["id"], "Repair attempt")["unblocked"]
    assert status == "triage"
    assert board.card_answer(triaged["id"], "Tool repaired and verified")["unblocked"]
    with closing(connection()) as conn:
        assert kb.claim_task(conn, triaged["id"]) is not None
    board.card_close(triaged["id"], "archived")
    # Duplicate notifications and racing callers cannot create multiple tasks.
    with ThreadPoolExecutor(max_workers=3) as pool:
        cards = list(pool.map(lambda _: board.card_create(**args)["card"], range(3)))
    task_id = cards[0]["id"]
    assert {card["id"] for card in cards} == {task_id}
    assert task_id in cards[0]["branch_name"]
    with TestCase().assertRaisesRegex(ValueError, "different arguments"):
        board.card_create(**{**args, "body": "Different work under the same command"})
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
    # Even an unconfirmed native stop revokes publication before returning.
    with patch("hermes_cli.kanban_db.archive_task", return_value=False):
        assert not board.card_close(task_id, "archived")["changed"]
    with patch("orchestrator.board.Research.call") as effect:
        for operation, arguments in (
            (board.workspace_integrate, (result_commit,)),
            (board.upload_build, ("build-evidence/verified", "policy")),
            (board.submit_requested_version, ("V-cancelled", "strategy.md", "Submit it", "league_selected")),
            (board.promotion_submit, ("E-cancelled", "league_selected")),
            (board.bootstrap_champion, ("V-cancelled", "league_selected")),
        ):
            with TestCase().assertRaises(ValueError):
                operation(task_id, result_run, *arguments)
        effect.assert_not_called()
    board.card_close(task_id, "archived")
    with patch("orchestrator.board.Research.call") as effect:
        with TestCase().assertRaises(ValueError):
            board.workspace_integrate(task_id, result_run, result_commit)
        effect.assert_not_called()
        with TestCase().assertRaises(ValueError):
            board.submit_requested_version(
                task_id, result_run, "V-cancelled", "strategy.md", "Submit it", "league_selected"
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
        unfinished = resolve_workspace(running, board="autoresearch")
        set_workspace_path(conn, running.id, unfinished)
        (unfinished / "unfinished-policy.bas").write_text("uncommitted policy work\n")
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
    assert (unfinished / "unfinished-policy.bas").read_text() == "uncommitted policy work\n"
    with closing(connection()) as conn:
        retained = kb.get_task(conn, running.id)
        assert retained.status == "archived"
        assert Path(retained.workspace_path) == unfinished
    # A remote effect admitted before cancellation remains reportable afterward.
    publishing = board.card_create(**{**args, "command_id": "race:publication"})["card"]
    with closing(connection()) as conn:
        publisher = kb.claim_task(conn, publishing["id"])
        publisher_run = publisher.current_run_id
        assert kb.complete_task(
            conn, publisher.id, summary="Build verified", expected_run_id=publisher_run
        )
    admitted, release = threading.Event(), threading.Event()

    def accepted_after_cancel(*args):
        admitted.set()
        assert release.wait(5)
        return {"submission_id": "accepted-before-cancellation"}

    with (
        patch("orchestrator.board.Research.call", side_effect=accepted_after_cancel),
        ThreadPoolExecutor() as pool,
    ):
        response = pool.submit(
            board.submit_requested_version,
            publisher.id,
            publisher_run,
            "V-ready",
            "strategy.md",
            "Submit",
            "league_selected",
        )
        assert admitted.wait(5)
        board.card_close(publisher.id, "archived")
        release.set()
        assert response.result()["submission_id"] == "accepted-before-cancellation"
    with closing(connection()) as conn:
        events = [event.kind for event in kb.list_events(conn, publisher.id)]
        assert events.index("submission_admitted") < events.index("publication_revoked")
    with patch("orchestrator.board.Research.call") as remote:
        with TestCase().assertRaises(ValueError):
            board.submit_requested_version(
                publisher.id, publisher_run, "V-ready", "strategy.md", "Submit", "league_selected"
            )
        remote.assert_not_called()
    game_cards(root, home, args)
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
