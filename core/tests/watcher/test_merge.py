"""Step 7, the mechanical merge: a reviewed Task's recorded commit reaches ``main`` only when
the merge is clean and the mixin's build and check pass on the result (design B.12)."""

from __future__ import annotations

from research_core import gitops
from research_core.store import tasks
from research_core.watcher import merge
from tests.watcher._support import events_of, make_ctx, make_task, set_setting, task_number
from tests.watcher._support_w3 import (
    branch_commit,
    policy_text,
    reviewed_task,
    setup_code_repo,
)


def merge_logs(ctx) -> list[str]:
    logs = ctx.coworld.documents / "logs"
    return sorted(p.name for p in logs.iterdir()) if logs.is_dir() else []


def test_clean_merge_moves_main_and_reruns_the_checks(tmp_path):
    ctx = make_ctx(tmp_path)
    code = setup_code_repo(ctx)
    old_main = gitops.rev_parse(code, "main")
    task = make_task_on_branch(ctx, aggression=4, patience=6)
    n = task_number(task)

    report = merge.run(ctx)

    assert [entry["task"] for entry in report["merged"]] == [task["id"]]
    new_main = gitops.rev_parse(code, "main")
    assert new_main != old_main
    parents = gitops.git(code, "rev-list", "--parents", "-n1", "main").stdout.split()
    assert parents[1:] == [old_main, task["completed_commit"]]  # a --no-ff merge commit
    assert (code / "policy.json").read_text() == policy_text(4, 6)
    record = tasks.read(ctx.store, n)
    assert record["merge"] == f"merged {new_main}"
    assert [e["payload"]["commit"] for e in events_of(ctx.store, "task.merged")] == [new_main]
    assert events_of(ctx.store, "merge.failed") == []
    names = merge_logs(ctx)
    assert any(name.startswith("build-") for name in names)
    assert any(name.startswith("check-") for name in names)
    assert gitops.is_clean(code)
    assert not any(w["path"].name == merge.CHECK_WORKTREE for w in gitops.list_worktrees(code))


def test_failing_check_leaves_main_unchanged_with_the_reason(tmp_path):
    ctx = make_ctx(tmp_path)
    code = setup_code_repo(ctx)
    old_main = gitops.rev_parse(code, "main")
    # check.py rejects a text parameter; build.sh runs it first, so the build is what fails.
    task = make_task_on_branch(ctx, aggression=4, patience="slow")

    report = merge.run(ctx)

    assert report["merged"] == []
    [failed] = report["failed"]
    assert failed["task"] == task["id"]
    assert failed["reason"] == "build failed with exit code 1"
    assert gitops.rev_parse(code, "main") == old_main
    assert gitops.is_clean(code)
    record = tasks.read(ctx.store, task_number(task))
    assert record["merge"] == f"not merged: {failed['reason']}"
    [event] = events_of(ctx.store, "merge.failed")
    assert event["payload"]["task"] == task["id"]
    assert event["payload"]["log"].endswith(".log")
    assert "is not a number" in event["payload"]["output_tail"]


def test_conflicting_branch_produces_merge_failed(tmp_path):
    ctx = make_ctx(tmp_path)
    code = setup_code_repo(ctx)
    task = make_task_on_branch(ctx, aggression=4, patience=5)
    (code / "policy.json").write_text(policy_text(9, 9))
    old_main = gitops.commit_all(code, "Main moves on")

    report = merge.run(ctx)

    [failed] = report["failed"]
    assert failed["files"] == ["policy.json"]
    assert "conflict" in failed["reason"]
    assert gitops.rev_parse(code, "main") == old_main
    assert gitops.is_clean(code)
    [event] = events_of(ctx.store, "merge.failed")
    assert event["payload"]["files"] == ["policy.json"]
    assert tasks.read(ctx.store, task_number(task))["merge"].startswith(
        "not merged: merge conflict"
    )


def test_replaced_worker_cannot_merge(tmp_path):
    """The recorded ``completed_commit`` is merged; a later commit on the branch never is."""
    ctx = make_ctx(tmp_path)
    code = setup_code_repo(ctx)
    task = make_task_on_branch(ctx, aggression=4, patience=6)
    n = task_number(task)
    recorded = task["completed_commit"]
    late = branch_commit(ctx, n, 1, 1, message="Late commit from the old run")
    assert late != recorded

    report = merge.run(ctx)

    assert [entry["task"] for entry in report["merged"]] == [task["id"]]
    assert gitops.is_ancestor(code, recorded, "main")
    assert not gitops.is_ancestor(code, late, "main")
    assert (code / "policy.json").read_text() == policy_text(4, 6)


def test_dirty_main_checkout_holds_the_merge(tmp_path):
    ctx = make_ctx(tmp_path)
    code = setup_code_repo(ctx)
    task = make_task_on_branch(ctx, aggression=4, patience=6)
    (code / "scratch.txt").write_text("uncommitted\n")

    report = merge.run(ctx)

    assert report["merged"] == [] and report["failed"] == []
    [held] = report["held"]
    assert held["task"] == task["id"] and "scratch.txt" in held["reason"]
    assert tasks.read(ctx.store, task_number(task))["merge"] == "pending"
    assert events_of(ctx.store, "merge.failed") == []
    assert any("scratch.txt" in note for note in ctx.notes)


def test_pause_skips_the_merge(tmp_path):
    ctx = make_ctx(tmp_path)
    setup_code_repo(ctx)
    task = make_task_on_branch(ctx, aggression=4, patience=6)
    set_setting(ctx, "paused", True)
    assert merge.run(ctx) == {"skipped": "paused"}
    assert tasks.read(ctx.store, task_number(task))["merge"] == "pending"


def test_missing_commit_is_a_failed_merge(tmp_path):
    ctx = make_ctx(tmp_path)
    setup_code_repo(ctx)
    task = reviewed_task(ctx, commit="0123abc4567def890123abc4567def8901234567")
    report = merge.run(ctx)
    [failed] = report["failed"]
    assert "not in the repository" in failed["reason"]
    assert tasks.read(ctx.store, task_number(task))["merge"].startswith("not merged:")


def make_task_on_branch(ctx, *, aggression, patience) -> dict:
    """A reviewed Task whose branch holds one policy commit; returns the Task record."""
    task = make_task(ctx, type="maintenance", title="Tune the starter")
    sha = branch_commit(ctx, task_number(task), aggression, patience)
    return reviewed_task(ctx, commit=sha, task=task)
