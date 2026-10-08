import functools

import pytest
from support.board import Board

from autoresearch.comms.envelope import MessageProblems
from autoresearch.comms.inbound import InboundProblems, submit_inbound
from autoresearch.comms.publisher import publish_hook
from autoresearch.comms.reader import read_since
from autoresearch.comms.stage import stage
from autoresearch.paths import Layout
from autoresearch.runtime.routing import route_all


def test_toy_kinds_end_to_end(tmp_path):
    board = Board(tmp_path)
    inbound_id = submit_inbound(board.canonical, "ping", {"text": "still there?"}, "preston")
    with pytest.raises(InboundProblems):
        submit_inbound(board.canonical, "ping", {"text": " "}, "preston")
    actions = route_all(board.ctx(), board.queue, allow_no_remote=True)
    assert ("acknowledged", inbound_id) in [(a.kind, a.inbound_id) for a in actions]

    worker = board.admit()
    worker.start()
    worker.write_journal()
    with pytest.raises(MessageProblems):
        stage(Layout(worker.worktree), "digest", "Week", "B", [], period="hourly")
    stage(Layout(worker.worktree), "digest", "Week 41", "Two versions.", [], period="weekly")
    worker.finish()
    [outcome] = board.process(publish=functools.partial(publish_hook, allow_no_remote=True))
    assert outcome.state == "published"
    [digest] = [m for m in read_since(board.canonical, "t", after=0) if m["kind"] == "digest"]
    assert digest["period"] == "weekly"
