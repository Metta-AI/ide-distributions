"""The ``ui`` tool: show, panel_set, notify and ask, and the conversation key (A3.5)."""

from __future__ import annotations

import pytest

import research_core.tools.ui  # noqa: F401  (registers the tool)
from research_core.store import notices, sessions, ui_state
from research_core.store.actors import Actor
from research_core.tools.ui import PANEL_KINDS

ANSWER_NOTE = "the answer arrives as a UI action; if no client can answer, ask in chat"


@pytest.fixture
def register_session(ctx_factory):
    def register(session_id, kind):
        ctx = ctx_factory()
        return sessions.register(
            ctx.wstore, Actor.harness(), session_id=session_id, profile="orchestrator", kind=kind
        )

    return register


def doc(ctx_factory, conversation):
    return ui_state.get(ctx_factory().wstore, conversation)["doc"]


def test_show_writes_under_the_chat_session(call, ctx_factory, register_session):
    register_session("s1", "chat")
    result = call("ui", {"action": "show", "ref": "T-3"}, session_id="s1")
    assert result["ok"] is True
    assert result["conversation"] == "s1"
    assert result["show"]["ref"] == "T-3" and result["show"]["at"]
    assert doc(ctx_factory, "s1")["show"]["ref"] == "T-3"
    assert "show" not in doc(ctx_factory, "workspace")

    viewed = call("ui", {"action": "show", "view": "leaderboard"}, session_id="s1")
    assert viewed["show"] == {"view": "leaderboard", "at": viewed["show"]["at"]}
    assert doc(ctx_factory, "s1")["show"] == viewed["show"]


def test_wake_and_unregistered_sessions_write_under_workspace(call, ctx_factory, register_session):
    register_session("w1", "wake")
    waked = call("ui", {"action": "show", "ref": "G-1"}, session_id="w1")
    assert waked["conversation"] == "workspace"
    unknown = call("ui", {"action": "show", "ref": "G-2"}, session_id="nobody")
    assert unknown["conversation"] == "workspace"
    assert doc(ctx_factory, "workspace")["show"]["ref"] == "G-2"
    assert ui_state.get(ctx_factory().wstore, "w1")["version"] == 0


def test_show_needs_exactly_one_of_ref_or_view(call):
    neither = call("ui", {"action": "show"})
    assert neither["ok"] is False and "ref" in neither["error"] and "view" in neither["error"]
    both = call("ui", {"action": "show", "ref": "T-1", "view": "v"})
    assert both["ok"] is False
    garbage = call("ui", {"action": "show", "ref": "the thing"})
    assert garbage["ok"] is False and "reference" in garbage["error"]


def test_panel_set_creates_the_container_and_keeps_other_panels(call, ctx_factory):
    assert set(PANEL_KINDS) >= {"markdown", "file", "agent-context"}
    first = call(
        "ui", {"action": "panel_set", "panel": "notes", "kind": "markdown", "content": "# Hi"}
    )
    assert first["ok"] is True and first["panel"] == "notes"
    assert first["conversation"] == "workspace"
    second = call(
        "ui",
        {"action": "panel_set", "panel": "table", "kind": "table", "content": {"rows": [[1]]}},
    )
    assert second["ok"] is True
    panels = doc(ctx_factory, "workspace")["panels"]
    assert panels["notes"]["kind"] == "markdown" and panels["notes"]["content"] == "# Hi"
    assert panels["table"]["content"] == {"rows": [[1]]}
    assert panels["notes"]["at"] and panels["table"]["at"]
    replaced = call(
        "ui", {"action": "panel_set", "panel": "notes", "kind": "markdown", "content": "# Bye"}
    )
    assert replaced["ok"] is True
    assert doc(ctx_factory, "workspace")["panels"]["notes"]["content"] == "# Bye"

    bad_kind = call("ui", {"action": "panel_set", "panel": "x", "kind": "pdf", "content": "a"})
    assert bad_kind["ok"] is False and "kind must be one of" in bad_kind["error"]


def test_file_panels_must_point_under_the_documents_folder(call, ctx_factory, tmp_path):
    documents = ctx_factory().coworld().documents
    (documents / "report.md").write_text("# Report\n")
    outside = tmp_path / "secret.txt"
    outside.write_text("no\n")

    relative = call(
        "ui", {"action": "panel_set", "panel": "doc", "kind": "file", "content": "report.md"}
    )
    assert relative["ok"] is True
    stored = doc(ctx_factory, "workspace")["panels"]["doc"]["content"]
    assert stored == str((documents / "report.md").resolve())

    absolute = call(
        "ui",
        {
            "action": "panel_set",
            "panel": "doc",
            "kind": "file",
            "content": str(documents / "report.md"),
        },
    )
    assert absolute["ok"] is True

    refused = call(
        "ui", {"action": "panel_set", "panel": "doc", "kind": "file", "content": str(outside)}
    )
    assert refused["ok"] is False and "documents" in refused["error"]
    escaped = call(
        "ui", {"action": "panel_set", "panel": "doc", "kind": "file", "content": "../secret.txt"}
    )
    assert escaped["ok"] is False
    missing = call(
        "ui", {"action": "panel_set", "panel": "doc", "kind": "file", "content": "nope.md"}
    )
    assert missing["ok"] is False and "does not exist" in missing["error"]
    assert ".state/" not in str(refused) + str(escaped) + str(missing)


def test_notify_writes_an_unread_notice_with_no_client_attached(call, ctx_factory):
    result = call(
        "ui",
        {"action": "notify", "text": "T-3 finished", "refs": ["T-3"], "urgency": "high"},
    )
    assert result["ok"] is True
    assert result["record"]["id"] == "N-1" and result["record"]["urgency"] == "high"
    wstore = ctx_factory().wstore
    unread = notices.unread(wstore)
    assert [n["text"] for n in unread] == ["T-3 finished"]
    assert unread[0]["refs"] == ["T-3"]
    kinds = [e.kind for e in wstore.events_after(0)]
    assert kinds.count("notice") == 1 and "ui.patch" not in kinds

    plain = call("ui", {"action": "notify", "text": "all quiet"})
    assert plain["record"]["urgency"] == "normal" and plain["record"]["refs"] == []
    bad = call("ui", {"action": "notify", "text": "x", "urgency": "loud"})
    assert bad["ok"] is False


def test_ask_opens_a_question_and_says_how_the_answer_arrives(call, ctx_factory, register_session):
    register_session("s1", "chat")
    result = call(
        "ui",
        {
            "action": "ask",
            "question": "Submit V-7 now?",
            "options": ["yes", "no"],
            "refs": ["V-7"],
        },
        session_id="s1",
    )
    assert result["ok"] is True
    ask_id = result["ask_id"]
    assert ask_id and result["conversation"] == "s1"
    assert result["next"] == ANSWER_NOTE
    asks = doc(ctx_factory, "s1")["asks"]
    assert asks[ask_id]["status"] == "open"
    assert asks[ask_id]["question"] == "Submit V-7 now?"
    assert asks[ask_id]["options"] == ["yes", "no"] and asks[ask_id]["refs"] == ["V-7"]

    again = call("ui", {"action": "ask", "question": "And V-8?"}, session_id="s1")
    assert again["ask_id"] != ask_id
    assert set(doc(ctx_factory, "s1")["asks"]) == {ask_id, again["ask_id"]}

    bad = call("ui", {"action": "ask", "question": "x", "options": [1, 2]})
    assert bad["ok"] is False


def test_researcher_cannot_use_ui(call):
    result = call("ui", {"action": "notify", "text": "hi"}, role="researcher")
    assert result["ok"] is False and "unknown tool 'ui'" in result["error"]
