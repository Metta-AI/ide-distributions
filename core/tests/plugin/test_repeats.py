"""The repeat hooks: a firmer refusal on the second identical one, a notice on a loop-guard halt."""

from __future__ import annotations

import json

import pytest

from research_core import layout
from research_core.plugin import repeats
from research_core.store import sessions
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.tools.base import argument_error
from research_core.tools.catalog import TOOLS, _load_tool_modules

HARNESS = Actor.harness()
CLAIM_ARGS = {"action": "write", "text": "the champion is pv_1"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("IDE_WORKSPACE", str(tmp_path / "ws"))
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})
    ws.ensure_dirs()
    return ws


def claim_refusal() -> str:
    """The real refusal of a ``claim write`` that leaves out its required fields."""
    _load_tool_modules()
    spec = TOOLS["claim"]
    payload = argument_error(
        spec,
        "orchestrator",
        "write",
        CLAIM_ARGS,
        ["summary is required for write", "tags is required for write"],
    )
    return json.dumps(payload)


def test_second_identical_refusal_is_replaced_and_the_first_and_successes_are_not():
    hook = repeats.make_hooks("orchestrator")["transform_tool_result"]
    refusal = claim_refusal()

    def call(result, args=CLAIM_ARGS, session_id="s-firm"):
        return hook(tool_name="claim", args=args, result=result, session_id=session_id)

    assert call(refusal) is None, "the first refusal keeps its full text and usage"
    second = json.loads(call(refusal))
    assert second["ok"] is False
    assert second["error"] == (
        "This exact call was refused 2 times. Supply summary, tags, or report the problem with "
        "ui.notify and end the run."
    )
    assert "usage" not in second and len(json.dumps(second)) < len(refusal)
    assert '"error"' in json.dumps(second)[:500], "Hermes's guard still counts it as a failure"
    assert "refused 3 times" in json.loads(call(refusal))["error"]

    assert call('{"ok": true, "id": "C-1"}') is None, "a success is left alone"
    assert call(refusal) is None, "a success resets the count for that call"
    assert call(refusal, args={**CLAIM_ARGS, "text": "other"}) is None, "other arguments"
    assert call(refusal, session_id="s-other") is None, "other Session"

    end = repeats.make_hooks("orchestrator")["on_session_end"]
    end(session_id="s-firm", turn_exit_reason="text_response(stop)")
    assert call(refusal) is None, "counts start over each turn, as Hermes's do"


def test_researcher_refusal_points_at_the_orchestrator():
    hook = repeats.make_hooks("researcher")["transform_tool_result"]
    refusal = json.dumps({"ok": False, "error": "x", "problems": ["unknown field 'y'"]})
    hook(tool_name="goal", args={"y": 1}, result=refusal, session_id="s-r")
    text = json.loads(hook(tool_name="goal", args={"y": 1}, result=refusal, session_id="s-r"))
    assert text["error"] == (
        "This exact call was refused 2 times. Fix what the refusal names (unknown field 'y'), "
        "or report the problem to the Orchestrator with task.ask and end the run."
    )


def test_transform_never_raises_and_ignores_non_refusals():
    hook = repeats.make_hooks("orchestrator")["transform_tool_result"]
    assert hook(tool_name="claim", args=None, result=None, session_id="s") is None
    assert (
        hook(tool_name="terminal", args={"command": "x"}, result="Exit code: 1", session_id="s")
        is None
    )
    assert (
        hook(tool_name="terminal", args={"command": "x"}, result="Exit code: 1", session_id="s")
        is None
    )
    assert hook(tool_name="claim", args=CLAIM_ARGS, result=claim_refusal()) is None  # no Session
    assert hook(tool_name="claim", args=object(), result="{", session_id="s") is None


def test_halt_writes_one_notice_naming_the_session_kind_and_tool(workspace):
    wstore = Store.open(workspace.store_path, kind="workspace")
    sessions.register(
        wstore,
        HARNESS,
        session_id="s-wake",
        profile="orchestrator",
        kind="wake",
        event="champion.changed",
    )
    hooks = repeats.make_hooks("orchestrator")
    refusal = claim_refusal()
    for _ in range(5):
        hooks["transform_tool_result"](
            tool_name="claim", args=CLAIM_ARGS, result=refusal, session_id="s-wake"
        )
    hooks["transform_tool_result"](
        tool_name="goal",
        args={},
        result='{"ok": false, "error": "x", "problems": ["x"]}',
        session_id="s-wake",
    )

    hooks["on_session_end"](session_id="s-wake", turn_exit_reason="guardrail_halt", completed=True)

    [notice] = _notices(wstore)
    assert "orchestrator's wake Session s-wake for champion.changed" in notice["text"]
    assert "claim kept failing" in notice["text"]
    assert notice["urgency"] == "high"


def test_a_normal_end_writes_no_notice(workspace):
    wstore = Store.open(workspace.store_path, kind="workspace")
    hooks = repeats.make_hooks("orchestrator")
    hooks["transform_tool_result"](
        tool_name="claim", args=CLAIM_ARGS, result=claim_refusal(), session_id="s-chat"
    )
    for reason in ("text_response(stop)", "interrupted", None):
        hooks["on_session_end"](session_id="s-chat", turn_exit_reason=reason)
    assert _notices(wstore) == []


def test_halt_hook_never_raises_without_a_workspace(tmp_path, monkeypatch):
    not_a_folder = tmp_path / "file"
    not_a_folder.write_text("x")
    monkeypatch.setenv("IDE_WORKSPACE", str(not_a_folder))  # no store can be opened there
    end = repeats.make_hooks("researcher")["on_session_end"]
    assert end(session_id="s", turn_exit_reason="guardrail_halt") is None


def _notices(store: Store) -> list[dict]:
    return [dict(row) for row in store.q("SELECT * FROM notices ORDER BY id")]
