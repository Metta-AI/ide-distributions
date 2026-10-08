"""Contract with the Softmax IDE bridge (``docs/integrations/softmax-ide-bridge.md`` on main;
compatibility notes in ``docs/integrations/softmax-ide-bridge-compat.md``).

The bridge is an L0 file adapter (Runtime 9.2): it writes ``comms/inbox/<id>.json`` itself,
whole then renamed, with its own id, and never commits. It reads ``comms/outbox/<seq>-<id>.json``
(``id``, ``seq``, ``kind``, ``title``, ``body``, ``in_reply_to``) and treats a ``note`` whose
``in_reply_to`` is one of its task ids as that task's acknowledgement.
"""

import functools
import json
import os

import jsonschema
import pytest
from support.board import Board

from autoresearch.comms.publisher import publish_hook
from autoresearch.comms.reader import read_since
from autoresearch.comms.stage import stage
from autoresearch.io import gitops
from autoresearch.paths import Layout
from autoresearch.queue.requests import load_schema
from autoresearch.runtime.routing import route_all

local_hook = functools.partial(publish_hook, allow_no_remote=True)
PLAYER = "ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1"
TASK_ID = f"{PLAYER}:task:tsk_42"
STOP_ID = f"{PLAYER}:stop:tsk_42"


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _envelope(message_id: str, kind: str) -> dict:
    return {
        "id": message_id,
        "created": "2026-10-08T20:00:00Z",
        "author": f"coach:{PLAYER}",
        "on_behalf_of": "human",
        "kind": kind,
    }


def bridge_task(**fields) -> dict:
    return _envelope(TASK_ID, "task") | {
        "title": "Profile macromackie's opening",
        "mode": "scouting",
        "why": "They lead the division. Done when an opponent model exists.",
        "refs": [],
        "priority": "P2",
    } | fields


def bridge_stop(title: str = "Profile macromackie's opening") -> dict:
    return _envelope(STOP_ID, "steer") | {
        "text": f"Stop task {TASK_ID}: {title}",
        "urgency": "now",
    }


def bridge_write(board: Board, message: dict) -> bytes:
    """Write like the bridge: the whole file to a temp name, then rename; no commit."""
    inbox = Layout(board.canonical).comms_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    data = json.dumps(message, indent=2).encode()
    temp = inbox / f".{message['id']}.json.tmp"
    temp.write_bytes(data)
    os.replace(temp, inbox / f"{message['id']}.json")
    return data


def _route(board: Board):
    return route_all(board.ctx(), board.queue, allow_no_remote=True)


def _committed(board: Board, relative: str) -> bytes | None:
    return gitops.show(board.canonical, "HEAD", relative)


def _delivery(board: Board, inbound_id: str) -> dict:
    record = board.committed(board.ledger / "inbound" / f"{inbound_id}.json")
    jsonschema.validate(record, load_schema("inbound.json"))
    return record


def _requests(board: Board) -> list[dict]:
    listing = gitops.git(board.canonical, "ls-tree", "--name-only", "HEAD", "cards/requests/")
    return [json.loads(gitops.show(board.canonical, "HEAD", path)) for path in listing.split()]


def _outbox_files(board: Board) -> list[tuple[str, dict]]:
    """``(file name, message)`` for each outbox file, read the way the bridge reads them."""
    outbox = board.canonical / "comms" / "outbox"
    return [(p.name, json.loads(p.read_text())) for p in sorted(outbox.glob("*.json"))]


def _finish_card_for(board: Board, request_id: str, ack_for: str | None = None):
    worker = board.admit_follow_up(request_id)
    _route(board)
    worker.start()
    worker.write_journal()
    if ack_for is not None:
        stage(Layout(worker.worktree), "note", "Stopped", "Dropped it.", [], in_reply_to=ack_for)
    worker.finish()
    [outcome] = board.process(publish=local_hook)
    assert outcome.state == "published"
    return worker


def test_bridge_task_becomes_a_card_and_its_note_closes_the_ide_task(board):
    written = bridge_write(board, bridge_task())

    actions = _route(board)

    assert ("adopted", TASK_ID) in [(a.kind, a.inbound_id) for a in actions]
    # Committed unchanged: the bridge owns the file and finds it again on a retry.
    assert _committed(board, f"comms/inbox/{TASK_ID}.json") == written
    [request] = _requests(board)
    assert request["spec"]["created_by"] == TASK_ID
    assert (request["spec"]["mode"], request["spec"]["priority"]) == ("scouting", "P2")
    record = _delivery(board, TASK_ID)
    assert record["state"] == "routed" and record["received_at"] == "2026-10-08T20:00:00Z"
    assert _route(board) == []  # a second run adopts and requests nothing

    _finish_card_for(board, request["id"])

    acks = [
        (name, message) for name, message in _outbox_files(board)
        if message.get("in_reply_to") == TASK_ID
    ]
    [(name, ack)] = acks
    assert ack["kind"] == "note" and ack["title"] and ack["body"]
    assert int(name.split("-", 1)[0]) == ack["seq"]  # the bridge skips by the name's seq
    assert name == f"{ack['seq']:08d}-{ack['id']}.json"
    assert _delivery(board, TASK_ID)["acknowledged_by"] == ack["id"]
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


def test_bridge_stop_steer_gets_an_urgent_card_that_must_answer_it(board):
    bridge_write(board, bridge_task())
    _route(board)
    bridge_write(board, bridge_stop())

    _route(board)

    stop_requests = [r for r in _requests(board) if r["spec"]["created_by"] == STOP_ID]
    [request] = stop_requests
    assert request["spec"]["priority"] == "P0"
    _finish_card_for(board, request["id"], ack_for=STOP_ID)
    [ack] = [m for _, m in _outbox_files(board) if m.get("in_reply_to") == STOP_ID]
    assert ack["kind"] == "note"
    assert _delivery(board, STOP_ID)["state"] == "acknowledged"


def test_an_invalid_bridge_task_is_answered_once_and_never_committed(board):
    bridge_write(board, bridge_task(mode="strategy"))  # an IDE area that is not a mode

    _route(board)
    _route(board)

    assert _committed(board, f"comms/inbox/{TASK_ID}.json") is None
    assert _requests(board) == []
    notes = [m for m in read_since(board.canonical, "t", after=0) if m["in_reply_to"] == TASK_ID]
    [note] = notes
    assert note["kind"] == "note" and note["from"] == "runtime" and "mode" in note["body"]


def test_a_partial_or_misnamed_write_is_not_adopted(board):
    inbox = Layout(board.canonical).comms_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{TASK_ID}.json").write_text('{"id": "')  # not JSON yet: skipped this run
    (inbox / f".{STOP_ID}.json.tmp").write_text("{}")  # the bridge's temp file

    assert _route(board) == []
    assert gitops.git(board.canonical, "ls-files", "comms/inbox") == ""
