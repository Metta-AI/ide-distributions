"""The ``ui`` tool (design A.8, A.10): what the Orchestrator shows, tells and asks the user.

Everything is a store write, never a wait on a browser: ``show`` and ``panel_set`` patch the
conversation's UI state document, ``ask`` opens a question in it, and ``notify`` writes a
notice. The conversation key follows A3.5: a chat Session writes under its own session id; a
wake Session, or one with no Session row, writes under the fixed conversation ``workspace``,
which every client sees. UI state and notices live in the workspace store.
"""

from __future__ import annotations

import json
import secrets
from importlib import resources
from pathlib import Path
from typing import Any

from research_core import refs
from research_core.store import notices, sessions, ui_state
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
WORKSPACE_CONVERSATION = "workspace"
ANSWER_NOTE = "the answer arrives as a UI action; if no client can answer, ask in chat"


def _panel_kinds() -> list[str]:
    """The panel kinds of the published UI-state contract (``contract/ui-state.schema.json``)."""
    schema_file = resources.files("research_core").joinpath("contract", "ui-state.schema.json")
    schema = json.loads(schema_file.read_text())
    return list(
        schema["properties"]["panels"]["additionalProperties"]["properties"]["kind"]["enum"]
    )


PANEL_KINDS = _panel_kinds()


def conversation_for(ctx: ToolContext) -> str:
    """A3.5: a chat Session's own id; ``workspace`` for a wake or an unregistered Session."""
    session_id = ctx.hermes_session_id()
    row = sessions.read(ctx.wstore, session_id) if session_id else None
    if row is not None and row["kind"] == "chat":
        return session_id
    return WORKSPACE_CONVERSATION


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    if call.action == "notify":
        record = notices.add(
            ctx.wstore,
            ctx.actor,
            text=call.get("text"),
            refs=call.get("refs") or [],
            urgency=call.get("urgency") or "normal",
        )
        return {"record": record}
    conversation = conversation_for(ctx)
    at = ctx.wstore.now()
    if call.action == "show":
        value = show_value(call.get("ref"), call.get("view"), at)
        record = patch(ctx, conversation, "/show", value)
        return {"conversation": conversation, "show": value, "version": record["version"]}
    if call.action == "panel_set":
        panel, kind = call.get("panel"), call.get("kind")
        content = call.get("content")
        if kind == "file":
            content = document_path(ctx, content)
        value = {"kind": kind, "content": content, "at": at}
        record = patch(ctx, conversation, f"/panels/{pointer_token(panel)}", value, "panels")
        return {"conversation": conversation, "panel": panel, "version": record["version"]}
    if call.action == "ask":
        options = call.get("options") or []
        if not all(isinstance(option, str) and option for option in options):
            raise ToolError("options is a list of short answer texts")
        ask_id = f"ask_{secrets.token_hex(4)}"
        value = {
            "question": call.get("question"),
            "options": options,
            "refs": call.get("refs") or [],
            "status": "open",
            "at": at,
        }
        record = patch(ctx, conversation, f"/asks/{ask_id}", value, "asks")
        return {
            "conversation": conversation,
            "ask_id": ask_id,
            "ask": value,
            "version": record["version"],
            "next": ANSWER_NOTE,
        }
    raise AssertionError(call.action)


def show_value(ref: str | None, view: str | None, at: str) -> dict[str, str]:
    if bool(ref) == bool(view):
        raise ToolError("show takes ref (a record such as T-3 or a platform id) or view, not both")
    if ref:
        if refs.parse(ref).kind == "unknown":
            raise ToolError(f"{ref!r} is not a reference: give a record id, platform id or URL")
        return {"ref": ref, "at": at}
    return {"view": view, "at": at}


def document_path(ctx: ToolContext, content: Any) -> str:
    """A file panel's content: an existing file under the Coworld documents folder, absolute."""
    if not isinstance(content, str) or not content.strip():
        raise ToolError("a file panel's content is a path under the Coworld documents folder")
    documents = ctx.coworld().documents.resolve()
    given = Path(content).expanduser()
    path = (given if given.is_absolute() else documents / given).resolve()
    if not path.is_relative_to(documents):
        raise ToolError(
            f"file panels show files under the Coworld documents folder; {content} is outside it"
        )
    if not path.is_file():
        raise ToolError(f"{content} does not exist under the Coworld documents folder")
    return str(path)


def patch(
    ctx: ToolContext, conversation: str, path: str, value: Any, container: str | None = None
) -> dict:
    """Add ``value`` at ``path``, creating the top-level ``container`` (``panels``, ``asks``)
    when the document has none yet: JSON Patch ``add`` needs the parent to exist."""
    ops: list[dict[str, Any]] = []
    if container is not None and container not in ui_state.get(ctx.wstore, conversation)["doc"]:
        ops.append({"op": "add", "path": f"/{container}", "value": {}})
    ops.append({"op": "add", "path": path, "value": value})
    return ui_state.patch(ctx.wstore, ctx.actor, conversation, ops)


def pointer_token(name: str) -> str:
    """A JSON Pointer token: ``~`` and ``/`` escaped, in that order (RFC 6901)."""
    return name.replace("~", "~0").replace("/", "~1")


SPEC = tool(
    ToolSpec(
        name="ui",
        description=(
            "What the user sees: bring a record or view into view, set a panel, send a notice, "
            "or ask a question the UI answers. Writes UI state; never waits on a client. A "
            "notice is the only channel a wake has to the user."
        ),
        handler=handle,
        roles=ORCHESTRATOR,
        executor="inproc",
        actions=(
            ActionSpec(
                "show",
                "Bring a record (ref) or a client view (view) into view.",
                {
                    "ref": a.string("A record such as T-3, a platform id or a platform URL."),
                    "view": a.string("A client view id, such as leaderboard."),
                },
            ),
            ActionSpec(
                "panel_set",
                "Put content in a named panel; kind is declared, never assumed. A file panel "
                "shows a file under the Coworld documents folder.",
                {
                    "panel": a.string("The panel's name."),
                    "kind": a.string("The content kind.", enum=PANEL_KINDS),
                    "content": {
                        "description": "The content, in the kind's shape (a path for file)."
                    },
                },
                ("panel", "kind", "content"),
            ),
            ActionSpec(
                "notify",
                "Tell the user something: the notice goes in text; it informs and never asks.",
                {
                    "text": a.string("The notice itself, in plain language."),
                    "refs": a.strings("Records the notice is about, such as T-3."),
                    "urgency": a.string(
                        "low, normal (default) or high.", enum=list(notices.URGENCIES)
                    ),
                },
                ("text",),
            ),
            ActionSpec(
                "ask",
                "Ask something only the user can answer; the first answer from any client wins.",
                {
                    "question": a.string("The question, in plain language."),
                    "options": a.strings("Short answers to offer, if any."),
                    "refs": a.strings("Records the question is about."),
                },
                ("question",),
            ),
        ),
    )
)
