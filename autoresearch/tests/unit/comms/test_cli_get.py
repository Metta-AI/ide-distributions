"""``comms get`` reads a published message or an inbound message by id."""

import json

import pytest
from support.board import Board

from autoresearch.cli import main
from autoresearch.comms.inbound import submit_inbound


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
