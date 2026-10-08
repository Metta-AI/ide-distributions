"""Envelope validation and worker staging (Task 3.5)."""

import pytest

from autoresearch.comms.envelope import KINDS, MessageProblems, build_message, message_problems
from autoresearch.comms.stage import Ref, stage
from autoresearch.ids import new_id
from autoresearch.io.docs import read_json
from autoresearch.paths import Layout


def test_stage_writes_a_valid_message(tmp_path):
    layout = Layout(tmp_path)
    claim = new_id("claim")
    message_id = stage(
        layout,
        "question",
        "Raise credits?",
        "We are out of XP credits.",
        [Ref(f"ar://claim/{claim}", role="subject", note="the blocker")],
        thread="credits",
        iteration=4,
    )
    message = read_json(layout.comms_staged / f"{message_id}.json")
    assert message["schema"] == "ar-comms/0" and message["from"] == "research-agent"
    assert message["iteration"] == 4 and message["priority"] == "P2"
    assert message["expects_response"] is True  # a question expects a response by default
    assert message["supersedes"] is None and message["expires"] is None
    assert "created" in message
    assert message["id"] == message_id and message["thread"] == "credits"
    assert message["refs"] == [
        {"uri": f"ar://claim/{claim}", "rev": "@result", "role": "subject", "note": "the blocker"}
    ]
    assert message_problems(message) == []


def test_the_outbound_kinds_are_system_design_7():
    assert set(KINDS) == {"note", "question", "suggestion", "help_request", "highlight"}


def test_every_known_kind_stages(tmp_path):
    for kind in KINDS:
        stage(Layout(tmp_path), kind, "T", "B", [])
    assert len(list(Layout(tmp_path).comms_staged.iterdir())) == len(KINDS)


@pytest.mark.parametrize(
    ("kind", "refs", "fields", "needle"),
    [
        ("finding", [], {}, "kind"),
        ("decision", [], {}, "kind"),
        ("seen", [], {}, "kind"),
        ("note", [Ref("ar://plan", role="aside")], {}, "refs/0/role"),
        ("note", [], {"priority": "high"}, "priority"),
        ("note", [], {"supersedes": "M-1"}, "supersedes"),
        ("note", [Ref("https://example.com")], {}, "refs/0/uri"),
        ("note", [Ref("ar://plan", rev="HEAD")], {}, "refs/0/rev"),
        ("note", [], {"in_reply_to": "nope"}, "in_reply_to"),
    ],
)
def test_stage_refuses_an_invalid_message(tmp_path, kind, refs, fields, needle):
    with pytest.raises(MessageProblems) as caught:
        stage(Layout(tmp_path), kind, "T", "B", refs, **fields)
    assert any(needle in problem for problem in caught.value.problems)
    assert not Layout(tmp_path).comms_staged.exists()


def test_outbox_messages_need_seq_and_a_real_rev():
    message = {
        "schema": "ar-comms/0",
        "id": new_id("message"),
        "kind": "note",
        "title": "T",
        "body": "",
        "refs": [{"uri": "ar://plan", "rev": "@result"}],
        "from": "runtime",
        "created": "2026-10-07T00:00:00+00:00",
        "iteration": None,
        "priority": "P2",
        "expects_response": False,
        "supersedes": None,
        "expires": None,
    }
    problems = message_problems(message, outbox=True)
    assert any("seq" in problem for problem in problems)
    assert any("@result" in problem for problem in problems)


def test_unknown_fields_are_allowed():
    message = {
        "schema": "ar-comms/0",
        "id": new_id("message"),
        "kind": "note",
        "title": "T",
        "body": "",
        "refs": [],
        "from": "research-agent",
        "created": "x",
        "iteration": 0,
        "priority": "P5",
        "expects_response": False,
        "supersedes": None,
        "expires": "2026-10-08T00:00:00+00:00",
        "future": {"a": 1},
    }
    assert message_problems(message) == []


@pytest.mark.parametrize(
    "missing", ["created", "iteration", "priority", "expects_response", "supersedes", "expires"]
)
def test_essential_fields_are_required(missing):
    message = build_message("note", "T", "B", [], "research-agent")
    del message[missing]
    assert any(missing in problem for problem in message_problems(message))


def test_from_must_be_research_agent_or_runtime():
    assert message_problems(build_message("note", "T", "B", [], "agent"))
