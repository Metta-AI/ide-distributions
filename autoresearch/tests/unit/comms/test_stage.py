"""Envelope validation and worker staging (Task 3.5)."""

import pytest

from autoresearch.comms.envelope import KINDS, MessageProblems, message_problems
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
    )
    message = read_json(layout.comms_staged / f"{message_id}.json")
    assert message["schema"] == "ar-comms/0" and message["from"] == "agent"
    assert message["id"] == message_id and message["thread"] == "credits"
    assert message["refs"] == [
        {"uri": f"ar://claim/{claim}", "rev": "@result", "role": "subject", "note": "the blocker"}
    ]
    assert message_problems(message) == []


def test_every_known_kind_stages(tmp_path):
    for kind in KINDS:
        stage(Layout(tmp_path), kind, "T", "B", [])
    assert len(list(Layout(tmp_path).comms_staged.iterdir())) == len(KINDS)


@pytest.mark.parametrize(
    ("kind", "refs", "fields", "needle"),
    [
        ("finding", [], {}, "kind"),
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
        "created_at": "2026-10-07T00:00:00+00:00",
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
        "from": "agent",
        "created_at": "x",
        "future": {"a": 1},
    }
    assert message_problems(message) == []
