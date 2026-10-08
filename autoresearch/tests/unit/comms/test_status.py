"""Derived status of outbound messages (System Design 7; Task 3.7)."""

import datetime

import pytest
from support.board import Board

from autoresearch.comms.inbound import StatusInfo, derived_status, submit_inbound
from autoresearch.comms.publisher import publish_runtime
from autoresearch.comms.reader import read_since
from autoresearch.runtime.ledger import canonical_lock

NOW = datetime.datetime(2026, 10, 7, 12, tzinfo=datetime.UTC)
PAST = "2026-10-07T00:00:00+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _publish(board: Board, **fields) -> str:
    with canonical_lock(board.canonical):
        publish_runtime(
            board.canonical, "question", "Q", "Body", [], allow_no_remote=True, **fields
        )
    return read_since(board.canonical, "t", after=0)[-1]["id"]


def _reply(board: Board, message_id: str) -> str:
    return submit_inbound(board.canonical, "reply", {"in_reply_to": message_id, "text": "y"}, "p")


def test_open_until_answered(board):
    question = _publish(board, expires=FUTURE)
    assert derived_status(board.canonical, question, now=NOW) == StatusInfo("open", False)
    _reply(board, question)
    assert derived_status(board.canonical, question, now=NOW) == StatusInfo("answered", False)


def test_expired_when_unanswered_past_expires(board):
    question = _publish(board, expires=PAST)
    assert derived_status(board.canonical, question, now=NOW) == StatusInfo("expired", False)


def test_a_reply_after_expiry_is_answered_and_late(board):
    question = _publish(board, expires="2020-01-01T00:00:00+00:00")
    _reply(board, question)
    assert derived_status(board.canonical, question, now=NOW) == StatusInfo("answered", True)


def test_withdrawn_when_a_later_message_supersedes_it(board):
    question = _publish(board)
    _publish(board, supersedes=question)
    assert derived_status(board.canonical, question, now=NOW) == StatusInfo("withdrawn", False)


def test_no_expiry_stays_open(board):
    question = _publish(board)
    assert derived_status(board.canonical, question, now=NOW).state == "open"
