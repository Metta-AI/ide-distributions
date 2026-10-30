"""Step 7: merge reviewed work into ``main`` with no model (design B.12, amendment A3.6).

A review wake that accepts a Task with ``merge: yes`` leaves ``tasks.merge == "pending"``.
This step integrates each such Task under the host-wide lock ``<workspace>/.state/merge.lock``:

* the commit merged is ``completed_commit``, the one ``task.complete`` recorded, never the
  branch tip, so late commits from a replaced run never reach ``main`` (B.13);
* ``main``'s checkout (``code/``) must be clean; otherwise the Task stays pending with a note
  and is tried again next tick (the fast-forward would touch that checkout);
* ``gitops.merge_no_ff`` merges in a temporary worktree and fast-forwards ``main``; then the
  mixin's ``build`` and ``check`` commands run in a temporary worktree of the merge result
  (``<coworld>/worktrees/merge-check``, removed afterwards), each with the A3.2 budget;
* both pass: ``tasks.set_merge("merged <sha>")`` (event ``task.merged``); a conflict, a
  failed command or a command that outlives its budget: ``main`` goes back to where it was,
  ``tasks.set_merge("not merged: <reason>")`` and the event ``merge.failed`` (a management
  wake resolves it in its own worktree, opens a maintenance Task or asks the user).

Orchestrator changes (``workspace.change_complete``) are maintenance Tasks and take the same
path. Pause stops this step. Nothing under ``code/`` is protected beyond what git protects.
"""

from __future__ import annotations

import os
import shutil
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from research_core import gitops, locks
from research_core import mixin as mixin_module
from research_core.gitops import GitError, MergeConflict
from research_core.store import settings, tasks
from research_core.watcher.resume import emit_event

if TYPE_CHECKING:  # pragma: no cover
    from research_core.watcher.context import TickContext

MERGE_LOCK = "merge.lock"
CHECK_WORKTREE = "merge-check"
CHECK_COMMANDS = ("build", "check")
CHECK_BUDGET_S = 240
MAIN = "main"


def run(ctx: TickContext) -> dict[str, Any]:
    """Integrate every complete Task whose review left ``merge == "pending"``."""
    if ctx.store is None:
        return {"skipped": "no active Coworld"}
    if settings.get(ctx.wstore, "paused"):
        return {"skipped": "paused"}
    pending = [t for t in tasks.list(ctx.store, status="complete") if t["merge"] == "pending"]
    report: dict[str, Any] = {"merged": [], "failed": [], "held": []}
    if not pending:
        return report
    with locks.file_lock(ctx.ws.state / MERGE_LOCK):
        for task in pending:
            result = integrate(ctx, task)
            report[result.pop("outcome")].append(result)
    return report


def integrate(ctx: TickContext, task: dict[str, Any]) -> dict[str, Any]:
    """Merge one Task's recorded commit into ``main`` and check the result.

    Returns ``{"outcome": "merged" | "failed" | "held", "task", ...}``. The caller holds the
    merge lock.
    """
    code = ctx.coworld.code
    commit = task["completed_commit"]
    if not commit:
        return _fail(ctx, task, "the Task recorded no completed commit")
    dirty = gitops.dirty_files(code)
    if dirty:
        shown = ", ".join(dirty[:5]) + (", …" if len(dirty) > 5 else "")
        reason = f"{task['id']} stays pending: main's checkout has uncommitted changes ({shown})"
        ctx.note(reason)
        return {"outcome": "held", "task": task["id"], "reason": reason}
    if not _commit_exists(code, commit):
        return _fail(ctx, task, f"commit {commit} is not in the repository")
    old_tip = gitops.rev_parse(code, f"refs/heads/{MAIN}")
    try:
        new_tip = gitops.merge_no_ff(
            code, commit, message=f"Merge {task['id']}: {task['title']}", into=MAIN
        )
    except MergeConflict as conflict:
        files = list(conflict.files)
        return _fail(ctx, task, f"merge conflict in {', '.join(files)}", files=files)
    except GitError as error:
        return _fail(ctx, task, f"git merge failed: {error}")
    failure = run_checks(ctx, new_tip)
    if failure is not None:
        _reset_main(code, old_tip, new_tip)
        reason = failure.pop("reason")
        return _fail(ctx, task, reason, **failure)
    tasks.set_merge(ctx.store, ctx.actor, task["id"], f"merged {new_tip}")
    if _submission_candidate(task):
        emit_event(
            ctx.store,
            ctx.actor,
            "submission.candidate",
            task["id"],
            {
                "task": task["id"],
                "commit": commit,
                "merge_commit": new_tip,
                "versions": list((task.get("checklist") or {}).get("versions") or []),
                "reason": task.get("submission_reason") or "",
            },
        )
    return {
        "outcome": "merged",
        "task": task["id"],
        "commit": commit,
        "merge_commit": new_tip,
        "previous_main": old_tip,
    }


def run_checks(
    ctx: TickContext, sha: str, *, commands: tuple[str, ...] = CHECK_COMMANDS
) -> dict[str, Any] | None:
    """Run the mixin ``commands`` at ``sha`` in a temporary worktree.

    ``None`` when every command passed; otherwise ``{"reason", "command"?, "log"?,
    "output_tail"?}`` for the first failure. The mixin is the one at ``sha`` (the merge may have
    changed it); a mixin that does not load is itself the failure.
    """
    coworld = ctx.coworld
    with temporary_worktree(coworld.code, coworld.worktrees / CHECK_WORKTREE, sha) as tree:
        try:
            loaded = mixin_module.load(tree / coworld.mixin.name)
        except mixin_module.MixinError as error:
            return {"reason": f"the mixin at {sha[:10]} is not usable: {'; '.join(error.problems)}"}
        for name in commands:
            if name not in loaded.commands:
                continue
            result = mixin_module.run_command(
                loaded,
                name,
                cwd=tree,
                budget_s=CHECK_BUDGET_S,
                log_dir=coworld.documents / "logs",
            )
            if not result.finished:
                _terminate(result.pid)
                return _command_failure(result, f"{name} did not finish within {CHECK_BUDGET_S} s")
            if not result.ok:
                return _command_failure(result, f"{name} failed with exit code {result.exit_code}")
    return None


@contextmanager
def temporary_worktree(repo: Path, path: Path, commit: str) -> Iterator[Path]:
    """A detached worktree of ``commit`` at ``path`` for the block, removed afterwards.

    A leftover at ``path`` from an interrupted earlier run is removed first, so one crash does
    not block every later merge or check.
    """
    gitops.git(repo, "worktree", "prune", check=False)
    if path.exists():
        gitops.git(repo, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gitops.git(repo, "worktree", "add", "--quiet", "--detach", str(path), commit)
    try:
        yield path
    finally:
        gitops.git(repo, "worktree", "remove", "--force", str(path), check=False)
        gitops.git(repo, "worktree", "prune", check=False)


# --- helpers ---------------------------------------------------------------------------------


def _submission_candidate(task: dict[str, Any]) -> bool:
    """Whether the Researcher answered ``submission_candidate: yes`` for this Task. The quality
    bar for a submission on the Orchestrator's own initiative needs the Version merged into
    ``main``, so the decision can only be taken once the merge landed (design B.6)."""
    checklist = task.get("checklist") or {}
    return (
        checklist.get("submission_candidate") == "yes" or task.get("submission_candidate") == "yes"
    )


def _fail(ctx: TickContext, task: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    """Record ``not merged: <reason>`` on the Task and emit ``merge.failed``."""
    reason = " ".join(reason.split())  # one line: the merge column's shape asks for it
    tasks.set_merge(ctx.store, ctx.actor, task["id"], f"not merged: {reason}")
    payload = {"task": task["id"], "reason": reason}
    payload.update({key: value for key, value in extra.items() if value is not None})
    emit_event(ctx.store, ctx.actor, "merge.failed", task["id"], payload)
    return {"outcome": "failed", **payload}


def _command_failure(result: mixin_module.CommandResult, reason: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "command": result.command,
        "log": str(result.log_path),
        "output_tail": result.output_tail,
    }


def _commit_exists(repo: Path, commit: str) -> bool:
    probe = gitops.git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    return probe.returncode == 0


def _reset_main(code: Path, old_tip: str, new_tip: str) -> None:
    """Put ``main`` back at ``old_tip`` after a failed check: a hard reset where it is checked
    out (that checkout was clean when the merge began), else a ref update."""
    if new_tip == old_tip:
        return
    checked_out = [w for w in gitops.list_worktrees(code) if w["branch"] == MAIN and not w["bare"]]
    if checked_out:
        gitops.git(checked_out[0]["path"], "reset", "--quiet", "--hard", old_tip)
    else:
        gitops.git(code, "update-ref", f"refs/heads/{MAIN}", old_tip, new_tip)


def _terminate(pid: int) -> None:
    """Stop a check command that outlived its budget (it runs in its own process group)."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
