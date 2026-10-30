"""The ``forum_post`` tool (design C.2): post or comment on the Softmax forum."""

from __future__ import annotations

from research_core.platform.wait import call_platform, identity_for
from research_core.tools import _args as a
from research_core.tools import _writes as w
from research_core.tools.base import ActionSpec, ToolCall, ToolSpec, tool

BOTH = frozenset({"orchestrator", "researcher"})


def handle(call: ToolCall) -> dict:
    ctx = call.ctx
    w.setting_allows(call, "platform_writes.forum")
    identity = identity_for(ctx, call.get("identity"))
    body = str(call.get("body"))
    platform = ctx.platform()
    if call.action == "post":
        title = str(call.get("title"))
        key = w.digest("post", title, body)
        intent = w.begin_intent(call, kind="forum", key=key, subject=None)
        if intent.get("existing") and intent.get("state") == "done":
            return {"post": intent.get("result"), "reconciled": True, "identity": identity}
        route = "POST posts"
        try:
            post = call_platform(
                ctx,
                route,
                lambda: platform.forum_post(
                    title=title, body=body, idempotency_key=key, identity=identity
                ),
                identity=identity,
            )
        except Exception as error:
            w.finish_intent(call, intent, state="failed", result={"error": str(error)})
            raise
        w.finish_intent(call, intent, state="done", result=post)
        what = f"forum post {post.get('id', '')}".strip()
        result = {"post": post, "identity": identity}
    else:
        post_id = str(call.get("post_id"))
        parent = call.get("parent_id")
        key = w.digest("comment", post_id, str(parent or ""), body)
        intent = w.begin_intent(call, kind="forum", key=key, subject=None)
        if intent.get("existing") and intent.get("state") == "done":
            return {"comment": intent.get("result"), "reconciled": True, "identity": identity}
        route = f"POST posts/{post_id}/comments"
        try:
            comment = call_platform(
                ctx,
                route,
                lambda: platform.forum_comment(
                    post_id, body=body, idempotency_key=key, parent_id=parent, identity=identity
                ),
                identity=identity,
            )
        except Exception as error:
            w.finish_intent(call, intent, state="failed", result={"error": str(error)})
            raise
        w.finish_intent(call, intent, state="done", result=comment)
        what = f"forum comment on {post_id}"
        result = {"comment": comment, "identity": identity}
    with ctx.store.tx(ctx.actor) as tx:
        tx.event(
            "platform.forum_post",
            what,
            {"action": call.action, "identity": identity, "cites": call.get("cites")},
        )
    w.notice_user_write(call, identity, what)
    return result


SPEC = tool(
    ToolSpec(
        name="forum_post",
        description=(
            "Post or comment on the Coworld's forum as your Player (or, for the Orchestrator, as "
            "the User). The platform_writes.forum setting bounds posts you make on your own; a "
            "user request (cites) always runs."
        ),
        handler=handle,
        roles=BOTH,
        executor="workspace",
        actions=(
            ActionSpec(
                "post",
                "Create a forum post.",
                {
                    "title": a.string("The post title."),
                    "body": a.string("The post body, Markdown."),
                    "identity": a.string("player (default) or user.", enum=["player", "user"]),
                    "cites": a.string("The user's message that asked for this, if any."),
                },
                ("title", "body"),
            ),
            ActionSpec(
                "comment",
                "Comment on a post, optionally replying to a comment.",
                {
                    "post_id": a.string("The post to comment on."),
                    "body": a.string("The comment, Markdown."),
                    "parent_id": a.string("The comment this replies to, if any."),
                    "identity": a.string("player (default) or user.", enum=["player", "user"]),
                    "cites": a.string("The user's message that asked for this, if any."),
                },
                ("post_id", "body"),
            ),
        ),
    )
)
