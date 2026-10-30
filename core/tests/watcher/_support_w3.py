"""Helpers for the merge, checks and panel tests, on top of ``_support.make_ctx``.

``setup_code_repo`` lays the Coworld's ``code/`` repository out as the setup wake leaves it: the
starter ``policy.json`` at the root and the fakegame mixin under ``mixin/``, committed on
``main``. ``branch_commit`` commits a policy on a Task branch in the Task's worktree, and
``reviewed_task`` walks a maintenance Task through claim, completion at a commit and an
accepting review, so the watcher's merge step finds ``merge == "pending"``.
"""

from __future__ import annotations

import json
from pathlib import Path

from research_core import gitops
from research_core.platform.fake import FakePlatform
from research_core.store import tasks
from research_core.store.actors import Actor
from research_core.watcher.context import TickContext
from tests.fixtures.fakegame_repo import COWORLD_ID, STARTER_POLICY, fixture_files
from tests.watcher._support import HARNESS, claim, make_task, register_session, task_number

SESSION = "api_1761690000_bbbb0001"


def setup_code_repo(ctx: TickContext) -> Path:
    """``code/`` on ``main``: the starter ``policy.json`` and the fakegame mixin."""
    code = gitops.init_repo(ctx.coworld.code)
    for relative, content in fixture_files(COWORLD_ID).items():
        path = code / "mixin" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (code / "policy.json").write_text(json.dumps(STARTER_POLICY, indent=2) + "\n")
    gitops.commit_all(code, "Import the fakegame mixin")
    return code


def policy_text(aggression, patience) -> str:
    return json.dumps({"parameters": {"aggression": aggression, "patience": patience}}) + "\n"


def branch_commit(ctx: TickContext, n: int, aggression, patience, message: str = "Tune") -> str:
    """Commit a policy on ``task/<n>`` in the Task's worktree; returns the new sha."""
    code = ctx.coworld.code
    tree = gitops.worktree_add(code, ctx.coworld.worktree_for_task(n), branch=f"task/{n}")
    (tree / "policy.json").write_text(policy_text(aggression, patience))
    return gitops.commit_all(tree, message)


def reviewed_task(
    ctx: TickContext, *, commit: str, session_id: str = SESSION, task: dict | None = None
) -> dict:
    """A maintenance Task completed at ``commit`` whose review accepted it with ``merge: yes``."""
    task = task or make_task(ctx, type="maintenance", title="Tune the starter")
    n = task_number(task)
    register_session(ctx, session_id, task)
    claim(ctx, session_id, task)
    tasks.complete(
        ctx.store,
        Actor.session(session_id),
        n,
        checklist={"commit": commit, "summary": "tuned the parameters"},
        commit=commit,
        merge_request="yes",
        result="the policy is tuned",
    )
    return tasks.review(ctx.store, HARNESS, n, accept=True, merge="yes", reason="looks right")


def platform_with_faults(ctx: TickContext, faults: str) -> FakePlatform:
    """The context's fake platform again, on the same state file, with ``IDE_FAKE_FAULTS``."""
    platform = FakePlatform(
        ctx.platform.state_path, env={"IDE_FAKE_FAULTS": faults}, clock=ctx.platform._clock
    )
    ctx.platform = platform
    return platform
