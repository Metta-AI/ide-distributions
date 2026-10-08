import functools

from support.board import Board

from autoresearch.comms.inbound import submit_inbound
from autoresearch.comms.publisher import publish_hook
from autoresearch.comms.stage import stage
from autoresearch.paths import Layout
from autoresearch.runtime.card_finish import finish
from autoresearch.runtime.handoff import ACK_REQUIRED_KINDS
from autoresearch.runtime.routing import route_all


def _record(board, inbound_id):
    return board.committed(board.ledger / "inbound" / f"{inbound_id}.json")


def test_a_card_acknowledged_toy_kind_goes_received_routed_acknowledged(tmp_path):
    assert "nudge" in ACK_REQUIRED_KINDS
    board = Board(tmp_path)
    inbound_id = submit_inbound(board.canonical, "nudge", {"text": "Check game 12."}, "preston")
    route_all(board.ctx(), board.queue, allow_no_remote=True)
    record = _record(board, inbound_id)
    assert record["state"] == "routed"
    request_id = record["responsible"]["request_id"]

    worker = board.admit_follow_up(request_id)
    route_all(board.ctx(), board.queue, allow_no_remote=True)
    worker.start()
    worker.write_journal()
    refused = finish(worker.ctx(), "done")  # no acknowledgement staged: card finish refuses
    assert not refused.ok and any(inbound_id in problem for problem in refused.problems)

    stage(Layout(worker.worktree), "note", "Checked", "Game 12 is fine.", [], in_reply_to=inbound_id)
    worker.finish()
    [outcome] = board.process(publish=functools.partial(publish_hook, allow_no_remote=True))
    assert outcome.state == "published"
    assert _record(board, inbound_id)["state"] == "acknowledged"
