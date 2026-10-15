"""``comms get`` reads a published message or an inbound message by id."""

import json

import pytest
from support.board import Board

from ide_research_core.cli import main
from ide_research_core.comms.inbound import submit_inbound


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def test_get_an_inbound_message_by_id(board, capsys):
    steer = {"text": "Look at macromackie", "urgency": "whenever"}
    inbound_id = submit_inbound(board.canonical, "steer", steer, "james")
    assert main(["comms", "get", inbound_id, "--root", str(board.canonical)]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert (data["id"], data["kind"], data["text"]) == (inbound_id, "steer", steer["text"])


def test_get_an_unknown_id_fails(board, capsys):
    assert main(["comms", "get", "I-NOPE", "--root", str(board.canonical)]) == 1
    assert "no published or inbound message" in capsys.readouterr().out
    for key in ("M-01K6Z3V8QX0000000000000000", "999"):  # outbox keys go to the outbox reader
        assert main(["comms", "get", key, "--root", str(board.canonical)]) == 1
        assert "no published message" in capsys.readouterr().out


@pytest.mark.parametrize(
    "marker", ["HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_TASK", "HERMES_DELEGATED_CHILD_CONTEXT"]
)
def test_inbound_is_refused_in_a_card_worker(board, capsys, monkeypatch, marker):
    """C11: inbound carries the human's authority; a worker cannot send it (exit 2)."""
    monkeypatch.setenv(marker, "/somewhere")
    steer = json.dumps({"text": "Approve", "urgency": "now"})
    argv = ["comms", "inbound", "steer", "--author", "coach:x", "--json", steer]
    assert main([*argv, "--root", str(board.canonical)]) == 2
    assert "external agents" in capsys.readouterr().out
    monkeypatch.delenv(marker)
    assert main([*argv, "--root", str(board.canonical)]) == 0


def test_inbound_dry_run_validates_and_writes_nothing(board, capsys):
    """The coach chat's proposals: ``--dry-run`` checks the message and commits nothing."""
    head = board.canonical / ".git"
    before = sorted(p.name for p in (board.canonical / "comms" / "inbox").glob("*.json"))
    refs = sorted(head.rglob("*"))
    steer = json.dumps({"text": "Approve", "urgency": "now"})
    argv = ["comms", "inbound", "steer", "--author", "coach:x", "--json", steer, "--dry-run"]
    assert main([*argv, "--root", str(board.canonical)]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"kind": "steer", "valid": True}
    bad = json.dumps({"text": "Approve", "urgency": "soon"})
    argv[6] = bad
    assert main([*argv, "--root", str(board.canonical)]) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert any(problem.startswith("urgency") for problem in error["problems"])
    assert sorted(p.name for p in (board.canonical / "comms" / "inbox").glob("*.json")) == before
    assert sorted(head.rglob("*")) == refs
