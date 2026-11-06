import json

import pytest

import research_core.tools.forum_post  # noqa: F401
import research_core.tools.settings  # noqa: F401
import research_core.tools.wiki_edit  # noqa: F401
from research_core.store import intents, notices
from research_core.store.actors import Actor
from research_core.tools import _writes
from research_core.tools.base import ToolContext, run_tool


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


@pytest.mark.parametrize("cites", ["G-3", "Goal G-3", "G-3, T-4"])
def test_citing_a_record_is_not_a_user_request(call, cites):
    set_setting(call, "platform_writes.forum", "never")
    args = {"action": "post", "title": "t", "body": "b", "cites": cites}
    refused = call("forum_post", args)
    assert refused["ok"] is False and "is a record" in refused["error"]
    set_setting(call, "platform_writes.forum", "ask")
    assert call("forum_post", args)["context"].get("ask_user") is True


def test_a_researcher_cannot_claim_a_user_request(call):
    set_setting(call, "platform_writes.wiki", "never")
    refused = call(
        "wiki_edit",
        {"wiki": "w", "page": "p", "title": "T", "body": "B", "cites": "the user asked"},
        role="researcher",
    )
    assert refused["ok"] is False and refused["context"].get("ask_orchestrator") is True
    assert "cites" not in refused["error"]


def test_a_sentence_that_mentions_a_record_is_a_user_request(call):
    set_setting(call, "platform_writes.forum", "never")
    cited = "the user asked: please post what T-3 found"
    out = call("forum_post", {"action": "post", "title": "t", "body": "b", "cites": cited})
    assert out["ok"] is True


def test_a_post_lands_in_the_coworlds_forum_and_shows_in_its_list(call, ctx_factory):
    ctx_factory()
    post = call("forum_post", {"action": "post", "title": "Hello", "body": "First"})
    assert post["ok"] is True, post
    listed = call("platform", {"action": "forum_list"})
    assert [row["id"] for row in listed["posts"]] == [post["post"]["id"]]
    elsewhere = call(
        "forum_post", {"action": "post", "title": "Hi", "body": "Second", "slug": "other"}
    )
    assert elsewhere["ok"] is True
    assert len(call("platform", {"action": "forum_list"})["posts"]) == 1


def test_a_changed_title_or_identity_is_a_new_write(call):
    first = {"wiki": "w", "page": "p", "title": "T", "body": "B"}
    assert call("wiki_edit", first)["ok"] is True
    retitled = call("wiki_edit", {**first, "title": "New title"})
    assert "reconciled" not in retitled  # it reached the platform: here a conflict, base unset
    assert retitled["conflict"] is True
    args = {"action": "post", "title": "t", "body": "b"}
    assert call("forum_post", args)["ok"] is True
    as_user = call("forum_post", {**args, "identity": "user"})
    assert "reconciled" not in as_user


def test_a_crash_before_the_notice_still_tells_the_user_on_retry(ctx_factory, monkeypatch):
    ctx = ctx_factory()
    user_ctx = ToolContext.from_env("orchestrator", dict(ctx.env, SOFTMAX_USER_CREDENTIAL="tok"))
    args = {"action": "post", "title": "t", "body": "b", "identity": "user"}

    def crash(*_, **__):
        raise RuntimeError("crash")

    with monkeypatch.context() as patch:
        patch.setattr("research_core.tools._writes.notices.add", crash)
        assert json.loads(run_tool("forum_post", args, user_ctx))["ok"] is False
    assert json.loads(run_tool("forum_post", args, user_ctx))["ok"] is True
    shown = notices.unread(ctx.wstore, limit=50, coworld_id="cow_fake")
    assert any("Acted as your User" in n["text"] for n in shown)


def test_wiki_conflict_and_user_identity_notice(call, ctx_factory):
    first = call("wiki_edit", {"wiki": "w", "page": "p", "title": "T", "body": "v1"})
    assert first["ok"] is True
    stale = call(
        "wiki_edit",
        {"wiki": "w", "page": "p", "title": "T", "body": "v2", "base_revision_id": "rev_stale"},
    )
    assert stale["ok"] is False and stale["conflict"] is True and stale["current_body"] == "v1"
    ctx = ctx_factory("orchestrator")
    before = len(notices.unread(ctx.wstore, limit=50, coworld_id="cow_fake"))
    env = dict(ctx.env, SOFTMAX_USER_CREDENTIAL="user-token")
    user_ctx = ToolContext.from_env("orchestrator", env)
    out = json.loads(
        run_tool(
            "forum_post",
            {"action": "post", "title": "as user", "body": "x", "identity": "user"},
            user_ctx,
        )
    )
    assert out["ok"] is True and out["identity"] == "user"
    assert len(notices.unread(ctx.wstore, limit=50, coworld_id="cow_fake")) == before + 1
    denied = call(
        "forum_post",
        {"action": "post", "title": "u", "body": "x", "identity": "user"},
        role="researcher",
    )
    assert denied["ok"] is False


def test_wiki_recovery_compares_the_title_too(call, ctx_factory):
    """An unsent title change is not done just because the body already matches."""
    args = {"wiki": "w", "page": "p", "body": "v1"}
    first = call("wiki_edit", {**args, "title": "Old"})
    assert first["ok"] is True
    args["base_revision_id"] = base = first["edit"]["revision_id"]
    ctx = ctx_factory("orchestrator")
    key = _writes.digest("wiki", "player", "w", "p", "New", base, "v1")
    intents.begin(ctx.store, Actor.harness(), kind="wiki", key=key)
    retry = call("wiki_edit", {**args, "title": "New"})
    assert retry["ok"] is True and "reconciled" not in retry
    assert ctx.platform().wiki_read("w", "p")["title"] == "New"


def test_wiki_recovery_still_tells_the_user(ctx_factory, call, monkeypatch):
    """A crash after the platform accepted the edit: the retry records and notices it once."""
    ctx = ctx_factory("orchestrator")
    env = dict(ctx.env, SOFTMAX_USER_CREDENTIAL="user-token")
    user_ctx = ToolContext.from_env("orchestrator", env)
    args = {"wiki": "w", "page": "p", "title": "T", "body": "v1", "identity": "user"}

    def crash(*_args, **_kwargs):
        raise RuntimeError("crash before the notice")

    with monkeypatch.context() as patch:
        patch.setattr("research_core.tools._writes.notices.add", crash)
        assert json.loads(run_tool("wiki_edit", args, user_ctx))["ok"] is False
    retried = json.loads(run_tool("wiki_edit", args, user_ctx))
    assert retried["ok"] is True and retried["reconciled"] is True
    shown = notices.unread(ctx.wstore, limit=50, coworld_id="cow_fake")
    assert sum("Acted as your User" in n["text"] for n in shown) == 1
    done = json.loads(run_tool("wiki_edit", args, user_ctx))
    assert done["reconciled"] is True
    shown = notices.unread(ctx.wstore, limit=50, coworld_id="cow_fake")
    assert sum("Acted as your User" in n["text"] for n in shown) == 1
