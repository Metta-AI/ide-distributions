"""The ``workspace`` tool (design C.2, B.11, B.15, B.17): the Coworld switch, change worktrees,
``doctor``, the capability list and the environment guide.

Executor ``workspace`` (A3.1): it clones repositories, reads the platform manifest and parses
YAML, so it runs in the workspace environment. ``capabilities {check: true}`` never runs the
checks here (A3.2, A3.4): it marks them due in the Coworld store's ``meta`` and the watcher runs
them on its next tick. ``change_complete`` (A3.6) is added by the review-and-merge entry.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from research_core import coworld_setup, environment_guide, gitops
from research_core.store import capabilities
from research_core.store._common import now_iso, parse_iso
from research_core.store.errors import SchemaMismatch
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolError, ToolSpec, tool

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store
    from research_core.tools.base import ToolContext

ORCHESTRATOR = frozenset({"orchestrator"})
BOTH = frozenset({"orchestrator", "researcher"})
CHECKS_DUE_KEY = "checks_due"
HEARTBEAT_KEY = "heartbeat"
HEARTBEAT_STALE_S = 180
_CHANGE_WORKTREE = re.compile(r"^orch-([1-9][0-9]*)$")


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    if call.action == "coworld_switch":
        return coworld_setup.switch(
            ctx,
            call.get("coworld"),
            cites=call.get("cites"),
            source=call.get("source"),
            commit=call.get("commit"),
            pending_request=call.get("pending_request"),
        )
    if call.action == "worktree_open":
        return open_worktree(ctx, call.get("name"))
    if call.action == "doctor":
        return doctor(ctx)
    if call.action == "capabilities":
        return list_capabilities(ctx, check=bool(call.get("check")))
    if call.action == "environment_guide":
        return {"guide": environment_guide.render(ctx)}
    raise AssertionError(call.action)


# --- worktree_open ----------------------------------------------------------------------------


def open_worktree(ctx: ToolContext, name: str | None) -> dict[str, Any]:
    """``worktrees/orch-<n>`` on branch ``orch/<n>`` from ``main``; ``name`` reopens one."""
    coworld = ctx.coworld()
    if not (coworld.code / ".git").exists():
        raise ToolError(
            "the Coworld's code repository does not exist yet; workspace.coworld_switch creates it"
        )
    if name:
        match = _CHANGE_WORKTREE.match(name)
        if match is None:
            raise ToolError(f"name must be a change worktree such as orch-2, not {name!r}")
        number = int(match.group(1))
    else:
        number = _next_change_number(coworld.code, coworld.worktrees)
    branch = f"orch/{number}"
    path = gitops.worktree_add(
        coworld.code, coworld.worktree_for_change(number), branch=branch, base="main"
    )
    return {
        "name": f"orch-{number}",
        "worktree": str(path),
        "branch": branch,
        "base": "main",
        "head": gitops.rev_parse(path, "HEAD"),
        "next": "Work and commit there; then workspace.change_complete {worktree, commit, summary}",
    }


def _next_change_number(code: Path, worktrees: Path) -> int:
    numbers = [0]
    if worktrees.is_dir():
        for entry in worktrees.iterdir():
            match = _CHANGE_WORKTREE.match(entry.name)
            if match:
                numbers.append(int(match.group(1)))
    refs = gitops.git(code, "for-each-ref", "--format=%(refname:short)", "refs/heads/orch/")
    for ref in refs.stdout.split():
        suffix = ref.removeprefix("orch/")
        if suffix.isdigit():
            numbers.append(int(suffix))
    return max(numbers) + 1


# --- doctor -----------------------------------------------------------------------------------


def doctor(ctx: ToolContext) -> dict[str, Any]:
    """Health facts with a flat ``problems`` list: commits, the watcher heartbeat, both stores'
    schema status, capabilities, CLI versions against the lock, stale skill overrides, whether
    the core path is writable (A7.7) and whether profiles' API keys agree (A5.8)."""
    env, ws = ctx.env, ctx.workspace
    checkout = environment_guide.core_checkout(env)
    coworld = ctx.coworld_or_none()
    report: dict[str, Any] = {
        "commits": {
            "image": environment_guide.image_info(env),
            "source": environment_guide.source_commit(checkout),
            "checkout": None if checkout is None else str(checkout),
        },
        "watcher": _heartbeat(ctx.wstore),
        "stores": {
            "workspace": _schema(ctx.wstore),
            "coworld": None if coworld is None else _coworld_schema(ctx),
        },
        "capabilities": [] if coworld is None else capabilities.status(ctx.store),
        "clis": _clis(ws.venv, checkout),
        "skill_overrides": [] if coworld is None else coworld_setup.skill_overrides(coworld.code),
        "core_path": None
        if checkout is None
        else {"path": str(checkout), "writable": os.access(checkout, os.W_OK)},
        "api_keys": _api_keys(env),
    }
    report["problems"] = _problems(report)
    return report


def _schema(store: Store) -> dict[str, Any]:
    status = store.schema_status()
    return {
        "ok": status.ok,
        "diff": status.diff,
        "user_version": status.user_version,
        "expected_version": status.expected_version,
    }


def _coworld_schema(ctx: ToolContext) -> dict[str, Any]:
    try:
        return _schema(ctx.store)
    except SchemaMismatch as error:
        return {"ok": False, "diff": error.problems}


def _heartbeat(wstore: Store) -> dict[str, Any]:
    value = wstore.get_meta(HEARTBEAT_KEY)
    if value is None:
        return {"heartbeat": None, "age_s": None}
    try:
        age = (parse_iso(now_iso()) - parse_iso(value)).total_seconds()
    except ValueError:
        return {"heartbeat": value, "age_s": None}
    return {"heartbeat": value, "age_s": round(age)}


def _clis(venv: Path, checkout: Path | None) -> dict[str, Any]:
    lock = environment_guide.lock_versions(checkout)
    entries = []
    for entry in environment_guide.cli_versions(venv):
        locked = lock.get(entry["distribution"])
        stale = bool(entry["version"] and locked and entry["version"] != locked)
        entries.append({**entry, "lock": locked, "stale": stale})
    return {"lock_refreshed": environment_guide.lock_refreshed(checkout), "clis": entries}


def _api_keys(env) -> dict[str, Any]:
    """Which profiles' ``.env`` carry the same ``API_SERVER_KEY`` as this one (names only)."""
    if not env.get("HERMES_HOME"):
        return {"checked": [], "differing": [], "missing": [], "note": "HERMES_HOME is not set"}
    home = Path(env["HERMES_HOME"])
    names = [home.name]
    names += [n.strip() for n in (env.get("IDE_RESEARCHER_PROFILES") or "researcher").split(",")]
    keys = {n: _env_value(home.parent / n / ".env", "API_SERVER_KEY") for n in names if n}
    reference = keys[home.name]
    return {
        "checked": list(keys),
        "differing": [
            n for n, k in keys.items() if k is not None and reference is not None and k != reference
        ],
        "missing": [n for n, k in keys.items() if k is None],
    }


def _env_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        text = line.strip().removeprefix("export ").strip()
        if text.startswith(f"{key}="):
            return text.partition("=")[2].strip().strip("'\"") or None
    return None


def _problems(report: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name, status in report["stores"].items():
        if status is not None and not status["ok"]:
            problems.append(
                f"{name} store schema differs from the release: {'; '.join(status['diff'])}"
            )
    watcher = report["watcher"]
    if watcher["heartbeat"] is None:
        problems.append("the watcher has not recorded a heartbeat yet")
    elif watcher["age_s"] is not None and watcher["age_s"] > HEARTBEAT_STALE_S:
        problems.append(f"the watcher's last heartbeat is {watcher['age_s']} s old")
    broken = [c["name"] for c in report["capabilities"] if c["status"] == "broken"]
    if broken:
        problems.append(f"broken capabilities: {', '.join(broken)}")
    refreshed = report["clis"]["lock_refreshed"] or "unknown date"
    for cli in report["clis"]["clis"]:
        if cli["stale"]:
            problems.append(
                f"{cli['name']}: installed {cli['version']}, lock {cli['lock']}, "
                f"lock refreshed {refreshed}"
            )
    stale = [o["name"] for o in report["skill_overrides"] if o["stale"]]
    if stale:
        problems.append(f"skill overrides whose core skill changed: {', '.join(stale)}")
    if report["core_path"] and report["core_path"]["writable"]:
        problems.append("core path writable: yes (hosting should mount the checkout read-only)")
    differing = report["api_keys"]["differing"]
    if differing:
        problems.append(f"API_SERVER_KEY differs from this profile's in: {', '.join(differing)}")
    return problems


# --- capabilities -----------------------------------------------------------------------------


def list_capabilities(ctx: ToolContext, *, check: bool) -> dict[str, Any]:
    store = ctx.store
    result: dict[str, Any] = {
        "capabilities": capabilities.status(store),
        "broken": capabilities.broken(store),
    }
    if check:
        due = store.now()
        store.set_meta(CHECKS_DUE_KEY, due)
        result["checks_due"] = due
        result["note"] = (
            "checks marked due; the watcher runs them on its next tick (within a minute) and "
            "the briefing shows the result"
        )
    return result


SPEC = tool(
    ToolSpec(
        name="workspace",
        description=(
            "The workspace itself: switch the active Coworld, open a change worktree, run the "
            "health check (doctor), list capabilities or ask for a new check, and read the "
            "environment guide (the first thing to consult when unsure about the environment)."
        ),
        handler=handle,
        roles=BOTH,
        executor="workspace",
        actions=(
            ActionSpec(
                "coworld_switch",
                "Make a Coworld the active one; setup starts when it was never set up.",
                {
                    "coworld": a.string("The platform Coworld id, such as cow_fake."),
                    "cites": a.string("The user's message reference when the user asked."),
                    "source": a.string(
                        "Override the source repository URL from the platform manifest."
                    ),
                    "commit": a.string("Override the pinned source commit."),
                    "pending_request": a.obj(
                        "{text, cites}: the user's request that setup carries out when done."
                    ),
                },
                ("coworld",),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "worktree_open",
                "Open worktrees/orch-<n> on branch orch/<n> from main for a change of your own.",
                {"name": a.string("An existing change worktree to reopen, such as orch-2.")},
                (),
                ORCHESTRATOR,
            ),
            ActionSpec("doctor", "Health facts and problems: commits, watcher, stores, CLIs."),
            ActionSpec(
                "capabilities",
                "List capabilities; check: true asks the watcher to run the checks next tick.",
                {"check": a.boolean("Mark the checks due (they run within a minute).")},
            ),
            ActionSpec("environment_guide", "Where you are, what is writable, credentials, CLIs."),
        ),
    )
)
