"""The ``softmax_api`` tool (design C.2): any Observatory route, with auth handled.

The escape hatch for routes no tool covers (the ``softmax-api-routes`` skill lists them). The
path is relative to ``/observatory``; the call runs as the Player, or as the User when the
Orchestrator asks (the Researcher is Player only). Every call is logged as a
``platform.request`` event in the Coworld store (method, path, identity, outcome, status), so
frequent routes can become proper tools. A non-GET call made as the User first writes a notice
(workspace store): "Acted as your User identity: <METHOD> <path>" (A4.7) — the user learns of
anything done in their name. Runs in the workspace environment.
"""

from __future__ import annotations

from research_core.platform.wait import call_platform, identity_for
from research_core.store import notices
from research_core.tools import _args as a
from research_core.tools.base import ToolCall, ToolError, ToolSpec, tool

BOTH = frozenset({"orchestrator", "researcher"})
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
READ_METHODS = ("GET", "HEAD")


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    method = str(call.get("method")).upper()
    path = normalise_path(call.get("path"))
    identity = identity_for(ctx, call.get("identity"))
    params, body = call.get("params"), call.get("json")
    if method not in METHODS:
        raise ToolError(f"method must be one of {', '.join(METHODS)}, not {method!r}")
    route = f"{method} {path}"
    if identity == "user" and method not in READ_METHODS:
        notices.add(
            ctx.wstore, ctx.actor, text=f"Acted as your User identity: {method} {path}", refs=()
        )
    platform = ctx.platform()
    try:
        response = call_platform(
            ctx,
            route,
            lambda: platform.request(method, path, params=params, json=body, identity=identity),
            identity=identity,
        )
    except ToolError as error:
        _log(call, method, path, identity, outcome="error", status=error.context.get("status"))
        raise
    _log(call, method, path, identity, outcome="ok", status=None)
    return {"method": method, "path": path, "identity": identity, "response": response}


def normalise_path(path: object) -> str:
    """``/v2/leagues``: a leading slash, the ``/observatory`` prefix dropped if given."""
    text = str(path or "").strip()
    if not text.startswith("/"):
        text = "/" + text
    return text.removeprefix("/observatory") or "/"


def _log(
    call: ToolCall, method: str, path: str, identity: str, *, outcome: str, status: int | None
) -> None:
    with call.ctx.store.tx(call.ctx.actor) as tx:
        tx.event(
            "platform.request",
            f"{method} {path}",
            {
                "method": method,
                "path": path,
                "identity": identity,
                "outcome": outcome,
                "status": status,
            },
        )


SPEC = tool(
    ToolSpec(
        name="softmax_api",
        description=(
            "Call any Softmax Observatory route with authentication handled, for routes no tool "
            "covers (see the softmax-api-routes skill). Paths are relative to /observatory. "
            "Runs as the Player; the Orchestrator may name identity user, which notifies the "
            "user for every non-GET call. Every call is logged."
        ),
        handler=handle,
        roles=BOTH,
        executor="workspace",
        params={
            "method": a.string("GET, HEAD, POST, PUT, PATCH or DELETE.", enum=list(METHODS)),
            "path": a.string("The route, such as /v2/leagues (relative to /observatory)."),
            "params": a.obj("Query parameters."),
            "json": a.obj("A JSON body for POST, PUT or PATCH."),
            "identity": a.string(
                "player (default) or user; the Researcher is Player only.",
                enum=["player", "user"],
            ),
        },
        required=("method", "path"),
    )
)
