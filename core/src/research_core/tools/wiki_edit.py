"""The ``wiki_edit`` tool (design C.2): a compare-and-swap edit of a Coworld wiki page."""

from __future__ import annotations

from research_core.platform.wait import call_platform, identity_for
from research_core.tools import _args as a
from research_core.tools import _writes as w
from research_core.tools.base import ToolCall, ToolSpec, tool

BOTH = frozenset({"orchestrator", "researcher"})


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    w.setting_allows(call, "platform_writes.wiki")
    identity = identity_for(ctx, call.get("identity"))
    wiki, page = str(call.get("wiki")), str(call.get("page"))
    title, body = str(call.get("title")), str(call.get("body"))
    base = call.get("base_revision_id")
    key = w.digest("wiki", wiki, page, str(base or ""), body)
    intent = w.begin_intent(call, kind="wiki", key=key, subject=None)
    platform = ctx.platform()
    if intent.get("existing") and intent.get("state") == "done":
        return {"edit": intent.get("result"), "reconciled": True, "identity": identity}
    if intent.get("existing") and intent.get("state") == "pending":
        # A crash after the platform accepted the edit: look for our body in the history.
        try:
            current = call_platform(ctx, "GET wiki page", lambda: platform.wiki_read(wiki, page))
            revision = (current.get("current_revision") or {}) if isinstance(current, dict) else {}
            if revision.get("body") == body:
                result = {"conflict": False, "page": current, "reconciled": True}
                w.finish_intent(call, intent, state="done", result=result)
                return {"edit": result, "reconciled": True, "identity": identity}
        except Exception:
            pass  # fall through and try the edit
    route = f"PUT wikis/{wiki}/pages/{page}"
    try:
        edit = call_platform(
            ctx,
            route,
            lambda: platform.wiki_edit(
                wiki,
                page,
                title=title,
                body=body,
                base_revision_id=base,
                idempotency_key=key,
                note=call.get("note"),
                identity=identity,
            ),
            identity=identity,
        )
    except Exception as error:
        w.finish_intent(call, intent, state="failed", result={"error": str(error)})
        raise
    if edit.get("conflict"):
        w.finish_intent(call, intent, state="failed", result={"conflict": True})
        return {
            "ok": False,
            "error": "the page changed since you read it; merge your edit into current_body "
            "and call again with current_revision_id as base_revision_id",
            "problems": ["wiki edit conflict"],
            "conflict": True,
            "current_revision_id": edit.get("current_revision_id"),
            "current_body": edit.get("current_body"),
        }
    w.finish_intent(call, intent, state="done", result=edit)
    what = f"wiki edit {wiki}/{page}"
    with ctx.store.tx(ctx.actor) as tx:
        tx.event("platform.wiki_edit", what, {"identity": identity, "cites": call.get("cites")})
    w.notice_user_write(call, identity, what)
    return {"edit": edit, "identity": identity}


SPEC = tool(
    ToolSpec(
        name="wiki_edit",
        description=(
            "Edit a Coworld wiki page with compare-and-swap: pass the revision you read as "
            "base_revision_id; a conflict returns the current body to merge. The "
            "platform_writes.wiki setting bounds edits you make on your own; a user request "
            "(cites) always runs."
        ),
        handler=handle,
        roles=BOTH,
        executor="workspace",
        params={
            "wiki": a.string("The wiki slug."),
            "page": a.string("The page slug."),
            "title": a.string("The page title."),
            "body": a.string("The whole new page body, Markdown."),
            "base_revision_id": a.string(
                "The revision you read (platform.wiki_read); omit to create a page."
            ),
            "note": a.string("A short edit note."),
            "identity": a.string("player (default) or user.", enum=["player", "user"]),
            "cites": a.string("The user's message that asked for this, if any."),
        },
        required=("wiki", "page", "title", "body"),
    )
)
