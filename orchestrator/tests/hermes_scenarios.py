"""Executed by Hermes's interpreter, against only the caller's temporary home."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from gateway.kanban_watchers_notifier import _WAKE_KINDS
from gateway.session_context import clear_session_vars, set_session_vars
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_boards import create_board
from hermes_cli.kanban_db_notify import list_notify_subs
from hermes_state import SessionDB

from orchestrator.board import Board, connection
from orchestrator.board import git as board_git
from orchestrator.context import Context
from orchestrator.gate import evaluate
from orchestrator.storage import WAKE_KINDS, snapshot


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def main():
    assert WAKE_KINDS == set(_WAKE_KINDS)
    home = Path(os.environ["HERMES_HOME"])
    root = home.parent.parent
    root.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    seed = root / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.name", "Orchestrator test")
    git(seed, "config", "user.email", "test@example.invalid")
    (seed / "first").write_text("first\n")
    git(seed, "add", "first")
    git(seed, "commit", "-m", "seed")
    remote = root / "canonical.git"
    git(root, "clone", "--bare", str(seed), str(remote))
    researcher = root / "profiles/researcher-1"
    researcher.mkdir()
    skill = researcher / "skills/experiment/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: experiment\ndescription: Run an experiment.\n---\nRecord evidence.\n"
    )
    collision = researcher / "skills/help/SKILL.md"
    collision.parent.mkdir(parents=True)
    collision.write_text("---\nname: help\ndescription: A preloadable skill.\n---\nRead me.\n")
    clone = researcher / "workspace"
    git(root, "clone", str(remote), str(clone))
    git(seed, "remote", "add", "origin", str(remote))
    (seed / "second").write_text("second\n")
    git(seed, "add", "second")
    git(seed, "commit", "-m", "advance main")
    git(seed, "push", "origin", "main")
    latest = git(seed, "rev-parse", "HEAD")
    create_board("autoresearch")
    context = Context(home, dict(os.environ))
    board = Board(context)
    state = SessionDB(db_path=home / "state.db")
    state.create_session("persisted-origin", "api_server")
    state.close()
    args = {
        "kind": "experimentation",
        "entity": "E-1",
        "assignee": "researcher-1",
        "title": "Test",
        "body": "Deliver evidence.",
        "skills": ["experiment"],
        "failure_limit": 2,
        "max_runtime_seconds": 7200,
        "session_id": "persisted-origin",
    }
    from orchestrator.plugin import handler

    for requested in (["policy-editing", "policy-upload", "practice"], ["experiment", "practice"]):
        rejected = json.loads(handler("card_create")({**args, "skills": requested}))
        assert not rejected["ok"], rejected
        assert rejected["error"]["reason"] == "invalid_worker_skills", rejected
        assert rejected["error"]["details"]["invalid"] == sorted(set(requested) - {"experiment"})
        assert board.card_list() == []
        assert git(clone, "branch", "--list", "card/*") == ""
    catalog = board.worker_skills("researcher-1")
    assert {"experiment", "help"} <= {s["name"] for s in catalog["skills"]}, catalog
    # A gate read must finish while create holds its lock around a slow Git fetch.
    fetching, release_fetch = Event(), Event()

    def slow_fetch(clone, *args):
        if args == ("fetch", "origin"):
            fetching.set()
            assert release_fetch.wait(10)
        return board_git(clone, *args)

    with (
        patch("orchestrator.board.git", side_effect=slow_fetch),
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        creates = [pool.submit(board.card_create, **args) for _ in range(2)]
        try:
            assert fetching.wait(10)
            gate = pool.submit(evaluate, context, now=datetime(2026, 10, 15, 12, 1, tzinfo=UTC))
            assert gate.result(timeout=2) == {"wakeAgent": False}
        finally:
            release_fetch.set()
        results = [future.result() for future in creates]
    task_id = results[0]["card"]["id"]
    assert results[1]["card"]["id"] == task_id
    assert len(board.card_list()) == 1
    assert not results[0]["subscribed"]
    task = results[0]["card"]
    assert task["branch_name"] == "card/experimentation-E-1-1"
    assert task["max_retries"] == 2 and task["max_runtime_seconds"] == 7200
    assert task["skills"] == ["experiment"]
    assert task["session_id"] == "persisted-origin"
    assert git(clone, "rev-parse", task["branch_name"]) == latest  # canonical main, fetched
    assert task["workspace_path"] == str(clone / ".worktrees/experimentation-E-1-1")
    try:
        board.card_create(**{**args, "attempt": 2})
    except ValueError as error:
        assert "already has open card" in str(error)
    else:
        raise AssertionError("duplicate entity accepted")

    tokens = set_session_vars(platform="api_server", chat_id="session-test", profile="orchestrator")
    try:
        replay = board.card_create(**args)
        assert replay["subscribed"], replay
    finally:
        clear_session_vars(tokens)
    with closing(connection()) as conn:
        subscriptions = list_notify_subs(conn, task_id)
        assert len(subscriptions) == 1
        assert subscriptions[0]["delivery_mode"] == "notify+wake"
        assert subscriptions[0]["chat_id"] == "session-test"
        assert kb.block_task(conn, task_id, reason="question: which baseline?")
    answered = board.card_answer(task_id, "Use current champion.")
    assert answered["unblocked"]
    assert board.card_show(task_id)["comments"][-1]["body"] == "Use current champion."
    assert board.card_show(task_id)["card"]["status"] == "ready"
    assert board.card_close(task_id, "done", summary="Completed with local evidence.")["changed"]
    late = board.card_comment(task_id, "highlight: late evidence")
    events = snapshot(context)["events"]
    assert not any(e["table"] == late["table"] and e["id"] == late["id"] for e in events)
    assert not any(e["table"] == "task_comments" for e in events)
    with closing(connection()) as conn:
        worker_comment = kb.add_comment(conn, task_id, "researcher-1", "highlight: late finding")
    events = snapshot(context)["events"]
    assert any(e["table"] == "task_comments" and e["id"] == worker_comment for e in events)
    assert snapshot(context)["events"] == events
    board.event_ack(events)
    assert snapshot(context)["events"] == []
    board.card_close(task_id, "archived")
    assert board.card_create(**args)["card"]["id"] == task_id

    # Repair base is the failed pushed commit, and a blocked original cannot gate it.
    failed = board.card_create(**{**args, "entity": "E-2"})["card"]["id"]
    with closing(connection()) as conn:
        kb.block_task(conn, failed, reason="integrate: overlap")
    repair = board.card_create(
        kind="repair",
        entity="repair-E-2",
        assignee="researcher-1",
        title="Repair",
        body="Original branch and reason.",
        card_sha=latest,
        parents=[failed],
    )["card"]
    assert repair["status"] == "ready"
    assert repair["branch_name"].startswith("repair/")
    assert git(clone, "rev-parse", repair["branch_name"]) == latest
    with closing(connection()) as conn:
        assert not conn.execute(
            "SELECT * FROM task_links WHERE child_id=?", (repair["id"],)
        ).fetchall()
    # A stale installed skill can invalidate an already queued card. Recovery uses
    # native operations, preserving dependency gates and replaying one new key.
    from hermes_cli.kanban_db_dispatch import _record_task_failure

    stale = board.card_create(**{**args, "entity": "E-stale"})["card"]["id"]
    dependent = board.card_create(
        **{**args, "entity": "E-dependent", "parents": [stale], "skills": ["help"]}
    )["card"]["id"]
    skill.unlink()
    cause = "Unknown skill(s): experiment"
    with closing(connection()) as conn:
        assert kb.claim_task(conn, stale, claimer="test")
        _record_task_failure(
            conn, stale, cause, outcome="crashed", force_trip=True, release_claim=True, end_run=True
        )
    original = board.card_show(stale)
    assert original["card"]["status"] == "blocked"
    assert original["card"]["last_failure_error"] == cause
    assert "help" in {s["name"] for s in board.worker_skills("researcher-1")["skills"]}
    assert "experiment" not in {s["name"] for s in board.worker_skills("researcher-1")["skills"]}
    retry_args = {
        **args,
        "entity": "E-stale",
        "attempt": 2,
        "skills": ["help"],
        "body": f"Replace {stale}: {cause}. Deliver evidence.",
        "replaces": stale,
    }
    # Losing the tool response after links commit must leave both cards gated.
    with (
        patch.object(kb, "archive_task", side_effect=RuntimeError("interrupted before archive")),
        TestCase().assertRaisesRegex(RuntimeError, "interrupted before archive"),
    ):
        board.card_create(**retry_args)
    attempts = [c for c in board.card_list() if c["idempotency_key"] == "experimentation:E-stale:2"]
    assert len(attempts) == 1
    assert attempts[0]["status"] == "todo"
    assert board.card_show(dependent)["card"]["status"] == "todo"
    replacement = board.card_create(**retry_args)["card"]
    assert replacement["id"] == attempts[0]["id"]
    assert replacement["id"] != stale
    assert replacement["status"] == "ready"
    assert replacement["skills"] == ["help"], replacement
    assert replacement["session_id"] == original["card"]["session_id"]
    assert stale in replacement["body"]
    assert board.card_create(**retry_args)["card"]["id"] == replacement["id"]
    assert board.card_show(dependent)["card"]["status"] == "todo"
    with closing(connection()) as conn:
        assert replacement["id"] in kb.parent_ids(conn, dependent)
    board.card_close(replacement["id"], "done", summary="Replacement built the policy")
    assert board.card_show(dependent)["card"]["status"] == "ready"
    after = board.card_show(stale)
    assert after["card"]["status"] == "archived"
    assert after["card"]["last_failure_error"] == cause, after
    assert [run["id"] for run in after["runs"]] == [run["id"] for run in original["runs"]]
    assert after["runs"][0]["error"] == cause
    assert after["runs"][0]["outcome"] == "gave_up"
    print(json.dumps({"ok": True, "cards": len(board.card_list({"include_archived": True}))}))


if __name__ == "__main__":
    main()
