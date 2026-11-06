"""The ``settings`` tool: reads with specs, cited updates on the store the key's scope names."""

from __future__ import annotations

import json

import research_core.tools.settings  # noqa: F401  (registers the tool)
from research_core.store import settings
from research_core.tools.base import run_tool


def test_read_all_lists_every_key_with_its_spec(call):
    result = call("settings", {"action": "read"}, role="researcher")
    assert result["ok"] is True
    by_key = {record["key"]: record for record in result["records"]}
    assert set(by_key) == set(settings.SETTINGS)
    paused = by_key["paused"]
    assert paused["value"] is False and paused["default"] is False
    assert paused["type"] == "bool" and paused["scope"] == "workspace"
    assert paused["description"]
    mode = by_key["submissions.mode"]
    assert mode["scope"] == "coworld" and mode["enum"] == ["ask", "autonomous"]


def test_an_update_must_send_a_value_and_null_clears(call):
    key = "xp.ask_above_requests_per_experiment"
    args = {"action": "update", "key": key, "cites": "the user said so in m-1"}
    refused = call("settings", args)
    assert refused["ok"] is False and "value is required" in refused["error"]
    assert call("settings", {**args, "value": 5})["ok"] is True
    assert call("settings", {**args, "value": None})["ok"] is True


def test_an_update_citing_a_record_is_refused(call):
    args = {"action": "update", "key": "paused", "value": True, "cites": "G-3"}
    refused = call("settings", args)
    assert refused["ok"] is False and "not the record G-3" in refused["error"]


def test_read_one_key(call):
    result = call("settings", {"action": "read", "key": "max_researchers"})
    assert result["record"]["key"] == "max_researchers" and result["record"]["value"] == 2
    unknown = call("settings", {"action": "read", "key": "nope"})
    assert unknown["ok"] is False and "unknown setting" in unknown["error"]


def test_update_lands_in_the_store_the_scope_names(call, ctx_factory):
    paused = call(
        "settings", {"action": "update", "key": "paused", "value": True, "cites": "msg-1"}
    )
    assert paused["ok"] is True
    assert paused["record"]["value"] is True and paused["record"]["previous"] is False
    assert paused["record"]["scope"] == "workspace"
    assert "next" not in paused

    mode = call(
        "settings",
        {"action": "update", "key": "submissions.mode", "value": "ask", "cites": "msg-2"},
    )
    assert mode["record"]["scope"] == "coworld"

    ctx = ctx_factory()
    assert settings.get(ctx.wstore, "paused") is True
    assert settings.get(ctx.store, "submissions.mode") == "ask"
    assert call("settings", {"action": "read", "key": "paused"})["record"]["value"] is True


def test_update_accepts_null_for_a_nullable_key(call):
    result = call(
        "settings",
        {"action": "update", "key": "submissions.max_count", "value": None, "cites": "msg-3"},
    )
    assert result["ok"] is True and result["record"]["value"] is None
    refused = call("settings", {"action": "update", "key": "paused", "value": None, "cites": "m"})
    assert refused["ok"] is False and "cannot be null" in refused["error"]


def test_update_refusals_name_every_problem(call):
    refused = call("settings", {"action": "update", "key": "paused", "value": "yes"})
    assert refused["ok"] is False
    assert "cites is required for update" in refused["problems"]
    typed = call("settings", {"action": "update", "key": "paused", "value": "yes", "cites": "m"})
    assert typed["ok"] is False and "must be a bool" in typed["error"]
    unknown = call("settings", {"action": "update", "key": "nope", "value": 1, "cites": "m"})
    assert unknown["ok"] is False and "unknown setting" in unknown["error"]


def test_researcher_cannot_update(call):
    denied = call(
        "settings",
        {"action": "update", "key": "paused", "value": True, "cites": "m"},
        role="researcher",
    )
    assert denied["ok"] is False and "not available to the researcher" in denied["error"]


def test_coworld_key_without_an_active_coworld(ctx_factory):
    ctx = ctx_factory("orchestrator", coworld=None)
    refused = json.loads(
        run_tool(
            "settings",
            {"action": "update", "key": "submissions.mode", "value": "ask", "cites": "m"},
            ctx,
        )
    )
    assert refused["ok"] is False
    assert "coworld setting" in refused["error"] and "no Coworld" in refused["error"]
    listed = json.loads(run_tool("settings", {"action": "read"}, ctx))
    assert {record["scope"] for record in listed["records"]} == {"workspace"}
