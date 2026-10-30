"""The one way a tool talks to the platform: retries, then a wait, then a refusal (C.4, A3.9).

:func:`call_platform` runs a platform call through ``with_retries`` (3 tries: 1 s, 4 s, 16 s).
When a :class:`PlatformError` still escapes, the tool refuses with the error's detail plus
``route``, ``status``, ``request_id`` and ``retryable``; a 401 also reports the ``login:<identity>``
capability as broken (A4.1). When the error is retryable (the platform stayed unreachable) and
the caller is a task Session holding its Task, the Task is first put on ``waiting_on: platform``
(``tasks.wait(kind="platform", detail="<route> <status>", ref=<request id>)``) so the Researcher
ends its run and the watcher resumes it when the route answers again. A non-retryable error
(404, 409, a missing identity) sets no wait: waiting would not change the answer; the agent
tries another route or asks.

:func:`identity_for` is the role rule every platform tool applies: the Researcher acts as the
Player only; a request for the User identity from a Researcher is refused with the reason.

Stdlib only (the plugin imports the tool modules that import this one); ``store.tasks`` is
imported when needed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from research_core.platform.errors import PlatformError
from research_core.platform.protocol import Identity, with_retries
from research_core.store.errors import StoreError
from research_core.tools.base import ToolContext, ToolError

if TYPE_CHECKING:  # pragma: no cover
    from research_core.store.db import Store

IDENTITIES: tuple[Identity, ...] = ("player", "user")
# Tests replace this to skip the backoff; ``call_platform`` reads it on every call.
sleep: Callable[[float], None] = time.sleep


def call_platform[T](
    ctx: ToolContext,
    route: str,
    fn: Callable[[], T],
    *,
    retries: bool = True,
    identity: Identity = "player",
) -> T:
    """Run ``fn`` with retries; a PlatformError that escapes becomes a ToolError (see above)."""
    try:
        return with_retries(fn, sleep=sleep) if retries else fn()
    except PlatformError as error:
        raise tool_error(ctx, route, error, identity=identity) from error


def tool_error(
    ctx: ToolContext, route: str, error: PlatformError, *, identity: Identity = "player"
) -> ToolError:
    """The refusal for a platform error, after the login report and the Task wait."""
    route = error.route or route
    context: dict[str, Any] = error.to_dict()
    context.pop("error", None)
    context["route"] = route
    if error.status == 401:
        context["login_failure"] = report_login_failure(ctx, identity, error)
    context.update(_wait_on_platform(ctx, route, error))
    return ToolError([error.detail], **context)


def report_login_failure(ctx: ToolContext, identity: str, error: PlatformError) -> dict | None:
    """Mark ``login:<identity>`` broken (A4.1); None when no Coworld store is active."""
    from research_core.store import capabilities

    store = _coworld_store(ctx)
    if store is None:
        return None
    report = capabilities.report_failure(
        store, ctx.actor, f"login:{identity}", output=error.detail, command="whoami"
    )
    return {"capability": report["name"], "maintenance_task": _task_id(report)}


def identity_for(ctx: ToolContext, requested: str | None) -> Identity:
    """The identity a call runs as: the Player unless the Orchestrator asks for the User."""
    identity = requested or "player"
    if identity not in IDENTITIES:
        raise ToolError(f"identity must be player or user, not {identity!r}")
    if identity == "user" and ctx.role == "researcher":
        raise ToolError(
            "the Researcher acts as the Player only; the User identity belongs to the "
            "Orchestrator (ask through task.ask or task.message)",
            identity="user",
            role=ctx.role,
        )
    return identity  # type: ignore[return-value]


# --- helpers -----------------------------------------------------------------------------


def _wait_on_platform(ctx: ToolContext, route: str, error: PlatformError) -> dict[str, Any]:
    """Put the caller's held Task on ``waiting_on: platform`` when the error persisted."""
    if not error.retryable or not ctx.actor.is_session:
        return {"waiting": False}
    task_id = _held_task_id(ctx)
    if task_id is None:
        return {"waiting": False}
    try:
        from research_core.store import tasks
    except ImportError:
        return {"waiting": False, "wait_skipped": "store.tasks is not available yet"}
    status = error.status if error.status is not None else "unreachable"
    try:
        tasks.wait(
            ctx.store,
            ctx.actor,
            task_id,
            kind="platform",
            detail=f"{route} {status}",
            ref=error.request_id,
        )
    except StoreError as problem:
        return {"waiting": False, "wait_skipped": "; ".join(problem.problems) or str(problem)}
    return {"waiting": True, "task": f"T-{task_id}"}


def _held_task_id(ctx: ToolContext) -> int | None:
    """The Task of a task Session that holds it and is in progress, else None."""
    from research_core.store import sessions

    session_id = ctx.actor.id
    session = sessions.read(ctx.wstore, session_id)
    if session is None or session["kind"] != "task" or not session["task_id"]:
        return None
    store = _coworld_store(ctx)
    if store is None:
        return None
    row = store.one("SELECT status, held_by FROM tasks WHERE id = ?", (session["task_id"],))
    if row is None or row["status"] != "in-progress" or row["held_by"] != session_id:
        return None
    return int(session["task_id"])


def _coworld_store(ctx: ToolContext) -> Store | None:
    try:
        return ctx.store
    except ToolError:
        return None


def _task_id(report: dict) -> str | None:
    task = report.get("maintenance_task")
    return None if not task else task["id"]
