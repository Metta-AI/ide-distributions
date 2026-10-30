import research_core.tools.forum_post  # noqa: F401
import research_core.tools.settings  # noqa: F401
import research_core.tools.wiki_edit  # noqa: F401
from research_core.store import notices


def set_setting(call, key, value):
    out = call("settings", {"action": "update", "key": key, "value": value, "cites": "msg-1"})
    assert out["ok"] is True, out


def test_post_and_comment_allowed_by_default(call):
    post = call("forum_post", {"action": "post", "title": "Hello", "body": "First post"})
    assert post["ok"] is True and post["post"]["id"]
    again = call("forum_post", {"action": "post", "title": "Hello", "body": "First post"})
    assert again["ok"] is True and again.get("reconciled") is True  # the intent is done
    comment = call(
        "forum_post",
        {"action": "comment", "post_id": post["post"]["id"], "body": "Nice"},
        role="researcher",
    )
    assert comment["ok"] is True and comment["comment"]["id"]


def test_ask_setting_asks_the_right_party_and_cites_overrides(call):
    set_setting(call, "platform_writes.forum", "ask")
    orch = call("forum_post", {"action": "post", "title": "t", "body": "b"})
    assert orch["ok"] is False and orch["context"].get("ask_user") is True
    res = call("forum_post", {"action": "post", "title": "t", "body": "b"}, role="researcher")
    assert res["ok"] is False and res["context"].get("ask_orchestrator") is True
    cited = call("forum_post", {"action": "post", "title": "t", "body": "b", "cites": "msg-9"})
    assert cited["ok"] is True


def test_never_setting_still_runs_a_user_request(call):
    set_setting(call, "platform_writes.wiki", "never")
    refused = call("wiki_edit", {"wiki": "w", "page": "p", "title": "T", "body": "B"})
    assert refused["ok"] is False and "user request overrides" in refused["error"]
    allowed = call(
        "wiki_edit", {"wiki": "w", "page": "p", "title": "T", "body": "B", "cites": "msg-2"}
    )
    assert allowed["ok"] is True and allowed["edit"]["conflict"] is False


def test_wiki_conflict_and_user_identity_notice(call, ctx_factory):
    first = call("wiki_edit", {"wiki": "w", "page": "p", "title": "T", "body": "v1"})
    assert first["ok"] is True
    stale = call(
        "wiki_edit",
        {"wiki": "w", "page": "p", "title": "T", "body": "v2", "base_revision_id": "rev_stale"},
    )
    assert stale["ok"] is False and stale["conflict"] is True and stale["current_body"] == "v1"
    ctx = ctx_factory("orchestrator")
    before = len(notices.unread(ctx.wstore, limit=50))
    env = dict(ctx.env, SOFTMAX_USER_CREDENTIAL="user-token")
    import json

    from research_core.tools.base import ToolContext, run_tool

    user_ctx = ToolContext.from_env("orchestrator", env)
    out = json.loads(
        run_tool(
            "forum_post",
            {"action": "post", "title": "as user", "body": "x", "identity": "user"},
            user_ctx,
        )
    )
    assert out["ok"] is True and out["identity"] == "user"
    assert len(notices.unread(ctx.wstore, limit=50)) == before + 1
    denied = call(
        "forum_post",
        {"action": "post", "title": "u", "body": "x", "identity": "user"},
        role="researcher",
    )
    assert denied["ok"] is False
