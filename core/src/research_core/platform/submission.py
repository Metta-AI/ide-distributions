"""Submission status and the autonomous quality bar (design §11, B.6, Appendix H).

A Version shows one submission status, computed and never stored: ``built`` -> ``uploaded`` ->
``submitted`` -> ``qualifying`` -> ``competing`` -> ``champion``. The first two come from the
Version record; the rest from the platform's submissions and memberships of its policy version.
Platform answers are cached for :data:`CACHE_TTL_S` per policy version in this process, so a
listing of recent Versions costs one round of reads. When the platform cannot be read, the
status falls back to the league's champion record (the last synced state) rather than failing:
the briefing and the clients must always render.

:func:`autonomous_allowed` is the rule for a submission on the agents' own initiative
(H "Submission paths"): ``submissions.mode`` is ``autonomous``, the window is open, the count
is below ``max_count``, the workspace is not paused and, while ``submissions.quality_bar`` is
on, the Version is merged into ``main`` and either a confirmed Experiment compares it with the
league's champion at a primary effect of at least ``min_margin_over_champion`` or, with no
champion, the mixin ``check`` command passes for its commit. Every failed reason ends with
"a user request overrides this": settings bound the agents' initiative, never the user.

Runs in the workspace environment (the mixin check needs it); the status part is stdlib only.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from research_core import gitops, mixin
from research_core.platform.errors import PlatformError
from research_core.store import capabilities, champion, settings
from research_core.store._common import display_id, parse_iso
from research_core.tools.base import ToolContext

if TYPE_CHECKING:  # pragma: no cover
    from research_core.platform.protocol import Membership, Platform, Submission
    from research_core.store.db import Store

LADDER = ("built", "uploaded", "submitted", "qualifying", "competing", "champion")
CACHE_TTL_S = 60.0
CHECK_BUDGET_S = 240
OVERRIDE = "a user request overrides this"

# policy_version_id -> (expires_at, status). One process, one cache: a tool subprocess starts
# empty; the watcher keeps it across ticks for the ten-minute champion sync and the panel.
_cache: dict[str, tuple[float, str]] = {}


# --- status --------------------------------------------------------------------------------


def status(store: Store, platform: Platform, record: dict[str, Any]) -> str:
    """The ladder position of a Version record (see the module docstring)."""
    uploaded = record.get("uploaded") or {}
    policy_version_id = uploaded.get("policy_version_id")
    if not policy_version_id:
        return "built"
    cached = _cache.get(policy_version_id)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    try:
        states = platform_states(platform, policy_version_id)
    except PlatformError:
        return _last_synced(store, record)
    found = status_from_states(states["submissions"], states["memberships"])
    _cache[policy_version_id] = (time.monotonic() + CACHE_TTL_S, found)
    return found


def platform_states(platform: Platform, policy_version_id: str) -> dict[str, list]:
    """The platform's submissions and memberships of one policy version."""
    return {
        "submissions": platform.submissions(policy_version_id=policy_version_id),
        "memberships": platform.memberships(policy_version_id=policy_version_id),
    }


def states_as_dicts(states: dict[str, list]) -> dict[str, list[dict]]:
    return {key: [asdict(item) for item in items] for key, items in states.items()}


def status_from_states(submissions: list[Submission], memberships: list[Membership]) -> str:
    """``champion`` > ``competing`` > ``qualifying`` > ``submitted`` > ``uploaded``.

    A membership that is the Player's champion wins; a ``competing`` membership is competing;
    a placed submission whose membership is still ``pending`` is qualifying; a submission the
    platform is still processing is submitted. A rejected submission alone leaves the Version
    uploaded.
    """
    if any(m.is_champion for m in memberships):
        return "champion"
    if any(m.status == "competing" for m in memberships):
        return "competing"
    placed = any(s.status == "placed" for s in submissions)
    pending_membership = any(m.status == "pending" for m in memberships)
    if placed and pending_membership:
        return "qualifying"
    if any(s.status in ("pending", "processing") for s in submissions):
        return "submitted"
    if pending_membership:
        return "qualifying"
    return "uploaded"


def invalidate(policy_version_id: str | None = None) -> None:
    """Forget cached statuses (one policy version, or all) after a write changed them."""
    if policy_version_id is None:
        _cache.clear()
    else:
        _cache.pop(policy_version_id, None)


def _last_synced(store: Store, record: dict[str, Any]) -> str:
    """The champion record's view of this Version when the platform cannot be read."""
    number = int(record["id"].split("-", 1)[1])
    row = store.one(
        "SELECT state FROM champion WHERE version_id = ? ORDER BY synced DESC LIMIT 1", (number,)
    )
    state = None if row is None else row["state"]
    if state == "champion":
        return "champion"
    if state == "placed":
        return "competing"
    if state in ("pending", "processing"):
        return "submitted"
    return "uploaded"


# --- the autonomous quality bar -------------------------------------------------------------


def autonomous_allowed(
    ctx: ToolContext, version: dict[str, Any], league_id: str, *, run_check: bool = True
) -> tuple[bool, list[str]]:
    """Whether the agents may submit ``version`` to ``league_id`` on their own initiative.

    ``reasons`` names every failed condition, each ending with "a user request overrides
    this". ``run_check=False`` skips the mixin check (used to compute warnings for a
    user-requested submission, which never waits on it).
    """
    found = shortfalls(ctx, version, league_id, run_check=run_check)
    return not found, [f"{reason}; {OVERRIDE}" for reason in found]


def shortfalls(
    ctx: ToolContext, version: dict[str, Any], league_id: str, *, run_check: bool = True
) -> list[str]:
    """The conditions of H "Submission paths" that ``version`` fails, as plain sentences."""
    stores = (ctx.wstore, ctx.store)
    found: list[str] = []
    mode = settings.get(stores, "submissions.mode")
    if mode != "autonomous":
        found.append(
            f"submissions.mode is {mode}: the Orchestrator asks before a submission on its own"
            " initiative"
        )
    until = settings.get(stores, "submissions.until")
    if until is not None and parse_iso(until) <= parse_iso(ctx.store.now()):
        found.append(f"the autonomous submission window ended at {until}")
    max_count = settings.get(stores, "submissions.max_count")
    if max_count is not None:
        made = autonomous_count(ctx.store)
        if made >= max_count:
            found.append(
                f"{made} of {max_count} autonomous submissions in this window are already used"
            )
    if settings.get(stores, "paused"):
        found.append("the workspace is paused")
    if settings.get(stores, "submissions.quality_bar"):
        found.extend(quality_bar_shortfalls(ctx, version, league_id, run_check=run_check))
    return found


def autonomous_count(store: Store) -> int:
    """Autonomous submissions in the current window.

    The window starts when the user last set ``submissions.until`` or ``submissions.max_count``
    (their ``settings.updated`` events); without such an event every autonomous submission
    counts.
    """
    since = store.one(
        "SELECT COALESCE(MAX(at), '') FROM events WHERE kind = 'settings.updated'"
        " AND subject IN ('submissions.until', 'submissions.max_count')"
    )[0]
    return store.one(
        "SELECT COUNT(*) FROM events WHERE kind = 'version.submitted'"
        " AND json_extract(payload, '$.autonomous') = 1 AND at >= ?",
        (since,),
    )[0]


def quality_bar_shortfalls(
    ctx: ToolContext, version: dict[str, Any], league_id: str, *, run_check: bool
) -> list[str]:
    vid = version["id"]
    commit = version.get("commit_sha")
    found: list[str] = []
    if not commit:
        found.append(
            f"{vid} was adopted from an upload made outside the workspace, so it has no"
            " commit to merge into main or to check"
        )
    elif not gitops.commit_on_branch(ctx.coworld().code, commit, "main"):
        found.append(f"{vid} (commit {commit[:7]}) is not merged into main")
    current = champion.read(ctx.store, league_id)
    if current is not None and current["state"] == "champion" and current["policy_version_id"]:
        found.extend(_experiment_shortfalls(ctx, vid, current))
    elif commit and run_check:
        problem = check_shortfall(ctx, vid, commit)
        if problem:
            found.append(problem)
    return found


def _experiment_shortfalls(ctx: ToolContext, vid: str, current: dict[str, Any]) -> list[str]:
    if current["version_id"] is None:
        return [
            f"the champion of {current['league_id']} (policy version"
            f" {current['policy_version_id']}) is not a Version in this workspace; version.adopt"
            " it to run an Experiment against it"
        ]
    baseline = display_id("V", current["version_id"])
    margin = settings.get((ctx.wstore, ctx.store), "submissions.min_margin_over_champion")
    if confirmed_against(ctx.store, vid, baseline, margin):
        return []
    return [
        f"no confirmed Experiment compares {vid} with the champion {baseline} at a primary"
        f" effect of at least {margin}"
    ]


def confirmed_against(store: Store, candidate: str, baseline: str, margin: float) -> bool:
    """A confirmed Experiment with these methods whose primary effects all reach ``margin``.

    ``effect`` in a measurement is the signed candidate-minus-baseline difference in the
    metric's favourable direction (the decision rule's convention: larger is better).
    """
    rows = store.q(
        "SELECT methods, results FROM experiments WHERE status = 'confirmed'"
        " AND json_extract(methods, '$.candidate') = ?"
        " AND json_extract(methods, '$.baseline') = ? ORDER BY id DESC",
        (candidate, baseline),
    )
    for row in rows:
        methods = json.loads(row["methods"] or "{}")
        results = json.loads(row["results"] or "{}")
        primaries = {m.get("name") for m in methods.get("metrics") or [] if _is_primary(m)}
        effects = [
            m.get("effect")
            for m in results.get("measurements") or []
            if isinstance(m, dict) and m.get("metric") in primaries
        ]
        if effects and all(isinstance(e, int | float) and e >= margin for e in effects):
            return True
    return False


def _is_primary(metric: Any) -> bool:
    return isinstance(metric, dict) and metric.get("role") == "primary"


def check_shortfall(ctx: ToolContext, vid: str, commit: str) -> str | None:
    """Run the mixin ``check`` command at ``commit``; None when it passes, else the reason.

    The command runs in a temporary detached worktree of the code repository, so no agent's
    tree is touched and the check sees exactly the Version's files. A failure reports the
    ``check`` capability (A4.1): a Version that built but does not check points at the
    toolchain more often than at the policy.
    """
    coworld = ctx.coworld()
    with tempfile.TemporaryDirectory(prefix="ide-check-") as temp:
        tree = Path(temp) / "check"
        gitops.git(coworld.code, "worktree", "add", "--quiet", "--detach", str(tree), commit)
        try:
            loaded = load_mixin_for(tree, coworld.mixin)
            result = mixin.run_command(
                loaded,
                "check",
                cwd=tree,
                log_dir=coworld.documents / "logs",
                budget_s=CHECK_BUDGET_S,
            )
        finally:
            gitops.git(coworld.code, "worktree", "remove", "--force", str(tree), check=False)
            gitops.git(coworld.code, "worktree", "prune", check=False)
    if not result.finished:
        return (
            f"the mixin check for {vid} did not finish within {CHECK_BUDGET_S} s"
            f" (log {result.log_path})"
        )
    if result.exit_code != 0:
        capabilities.report_failure(
            ctx.store,
            ctx.actor,
            "check",
            output=_log_text(result.log_path),
            command=result.command,
        )
        return (
            f"the mixin check for {vid} failed with exit code {result.exit_code}"
            f" (log {result.log_path}): {result.output_tail.strip()[-300:]}"
        )
    return None


def load_mixin_for(tree: Path, fallback: Path) -> mixin.Mixin:
    """The mixin of a working tree (``<tree>/mixin``), else the Coworld's ``code/mixin``."""
    own = tree / "mixin"
    return mixin.load(own if (own / mixin.FRONT_MATTER_FILE).is_file() else fallback)


def _log_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
