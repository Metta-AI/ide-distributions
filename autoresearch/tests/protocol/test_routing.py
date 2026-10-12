"""Inbound routing, acknowledgement and delivery state (Task 3.7, Overview C11): real git
canonical, FileQueue and simulated workers (``tests/support/board.py``)."""

import dataclasses
import functools
import json

import jsonschema
import pytest
from support.board import Board

from autoresearch.comms import publisher, runtime_messages
from autoresearch.comms.inbound import derived_status, submit_inbound
from autoresearch.comms.publisher import publish_hook, publish_runtime
from autoresearch.comms.reader import read_since, seen_path
from autoresearch.comms.stage import stage
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.requests import load_schema
from autoresearch.runtime import blocked, routing
from autoresearch.runtime.briefing import BriefingInputs, build_briefing
from autoresearch.runtime.handoff import card_spec
from autoresearch.runtime.ledger import canonical_lock
from autoresearch.runtime.routing import (
    _routing_order,
    inbound_for_briefing,
    planning_backlog,
    route_all,
)

local_hook = functools.partial(publish_hook, allow_no_remote=True)
TOPIC = {"title": "Towers", "objective": "Stop dying to towers.", "metrics": [], "priority": "P2"}


class Crash(RuntimeError):
    pass


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _send(board: Board, kind: str, author: str = "preston", **payload) -> str:
    return submit_inbound(board.canonical, kind, payload, author)


def _route(board: Board):
    return route_all(board.ctx(), board.queue, allow_no_remote=True)


def _delivery(board: Board, inbound_id: str) -> dict:
    record = board.committed(board.ledger / "inbound" / f"{inbound_id}.json")
    jsonschema.validate(record, load_schema("inbound.json"))
    return record


def _requests(board: Board) -> list[dict]:
    """Card requests committed at canonical HEAD, except those of cards ``Board.admit``
    admitted (created by "planner")."""
    listing = gitops.git(board.canonical, "ls-tree", "--name-only", "HEAD", "cards/requests/")
    requests = [json.loads(gitops.show(board.canonical, "HEAD", path)) for path in listing.split()]
    return [r for r in requests if (r.get("spec") or {}).get("created_by") != "planner"]


def _outbox(board: Board) -> list[dict]:
    return read_since(board.canonical, "test", after=0, include_unknown=True)


def _question(board: Board, **fields) -> str:
    with canonical_lock(board.canonical):
        publish_runtime(
            board.canonical,
            "question",
            "Raise credits?",
            "Yes or no.",
            [],
            allow_no_remote=True,
            expects_response=True,
            **fields,
        )
    return _outbox(board)[-1]["id"]


def _card_file(board: Board, task_id: str) -> dict:
    return read_json(board.canonical / ".supervisor" / "runtime" / "cards" / f"{task_id}.json")


def _assert_clean(board: Board) -> None:
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


# --- task ---


def test_valid_task_becomes_its_own_card_request(board):
    ref = f"ar://hypothesis/{board.hypothesis}"
    task = _send(
        board,
        "task",
        "ide",
        title="Profile andre",
        mode="scouting",
        why="Leads.",
        refs=[ref],
        priority="P1",
    )

    actions = _route(board)

    [request] = _requests(board)
    spec = request["spec"]
    assert spec["created_by"] == task and f"[task:{task}]" in spec["why"]
    assert (spec["title"], spec["mode"], spec["refs"], spec["priority"]) == (
        "Profile andre",
        "scouting",
        [ref],
        "P1",
    )
    record = _delivery(board, task)
    assert record["state"] == "routed"
    assert record["responsible"] == {"task_id": None, "request_id": request["id"], "backlog": False}
    assert [entry["state"] for entry in record["history"]] == ["received", "routed"]
    assert any(a.kind == "card_requested" and a.request_id == request["id"] for a in actions)
    _assert_clean(board)

    assert _route(board) == [] and len(_requests(board)) == 1

    worker = board.admit_follow_up(request["id"])
    _route(board)
    assert _delivery(board, task)["responsible"]["task_id"] == worker.task_id
    assert _card_file(board, worker.task_id)["responsible_inbound"] == [task]


def test_task_is_acknowledged_by_the_runtime_when_its_card_publishes(board):
    task = _send(
        board, "task", "ide", title="Scout", mode="scouting", why="W", refs=[], priority="P1"
    )
    _route(board)
    [request] = _requests(board)
    worker = board.admit_follow_up(request["id"])
    worker.start()
    worker.write_journal()
    worker.finish()

    [outcome] = board.process(publish=local_hook)

    assert outcome.state == "published"
    [ack] = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert ack["kind"] == "note" and ack["from"] == "runtime"
    # The task as the sender phrased it, then the card's own summary (its journal's opening).
    assert (ack["title"], ack["body"]) == ("Done: Scout", "Did things.")
    handoff = worker.handoff()
    assert ack["about"] == {
        "card": worker.task_id,
        "request": request["id"],
        "merge_commit": handoff["merge_sha"],
        "outcome": "done",
    }
    journal = handoff["journal"].rsplit("/", 1)[-1].removesuffix(".md")
    assert [(ref["uri"], ref["rev"]) for ref in ack["refs"]] == [
        (f"ar://handoff/{worker.task_id}/{worker.run_id}", ack["published_rev"]),
        (f"ar://journal/{journal}", handoff["merge_sha"]),
    ]
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == ack["id"]
    _assert_clean(board)
    board.process(publish=local_hook)  # nothing left: no second acknowledgement
    assert len([m for m in _outbox(board) if m.get("in_reply_to") == task]) == 1


def _finished_task_card(board: Board, title: str = "Scout"):
    task = _send(board, "task", "ide", title=title, mode="scouting", why="W", refs=[], priority="P1")
    _route(board)
    [request] = _requests(board)
    worker = board.admit_follow_up(request["id"])
    worker.start()
    worker.write_journal()
    return task, worker


def test_task_acknowledgement_quotes_the_runs_summary_and_the_cards_messages(board):
    task, worker = _finished_task_card(board)
    stage(Layout(worker.worktree), "highlight", "Towers win lanes", "Found it.", [])
    worker.finish(outcome="partial", terminal=False)
    board.queue.finish(worker.task_id, worker.run_id, "partial", summary="Towers win.")

    board.process(publish=local_hook)

    [ack] = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert ack["title"] == "Partly done: Scout"
    assert ack["body"] == (
        "Towers win. The rest of the task is not done yet. "
        "The agent also sent: “Towers win lanes”."
    )


def test_a_blocked_cards_ack_prefers_its_journal_over_the_block_reason(board):
    task, worker = _finished_task_card(board)
    worker.finish(outcome="blocked", terminal=False)
    reason = "tool-fault: softmax build: FileNotFoundError"
    board.queue.finish(worker.task_id, worker.run_id, "blocked", summary=reason)

    board.process(publish=local_hook)

    [ack] = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert ack["body"].startswith("Did things. ") and reason not in ack["body"]


def test_a_summary_that_names_ids_gives_way_to_the_journal_or_is_made_plain(board):
    task, worker = _finished_task_card(board)
    stage(Layout(worker.worktree), "help_request", "Stuck on H-01K6Z3V8QX0000000000000000", "B", [])
    worker.finish(outcome="blocked", terminal=False)
    ids_summary = f"Created {board.hypothesis} and requested its experiment card."
    board.queue.finish(worker.task_id, worker.run_id, "blocked", summary=ids_summary)

    board.process(publish=local_hook)

    [ack] = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert ack["body"] == (
        "Did things. The agent needs help to go on; its help request says what it needs. "
        "The agent also sent: “Stuck on the hypothesis”."
    )
    assert runtime_messages.plainest_summary([ids_summary, f"See ar://claim/C-x at {'a1' * 6}."]) == (
        "Created the hypothesis and requested its experiment card."
    )


def test_invalid_task_gets_a_runtime_note_with_its_errors(board):
    missing = "ar://hypothesis/H-01K6Z3V8QX0000000000000000"
    task = _send(
        board, "task", "ide", title="T", mode="scouting", why="W", refs=[missing], priority="P1"
    )

    _route(board)

    assert _requests(board) == []
    [note] = _outbox(board)
    assert note["kind"] == "note" and note["in_reply_to"] == task and note["from"] == "runtime"
    assert missing in note["body"] and note["title"] == "Not started: T"
    assert note["about"] == {"outcome": "rejected"} and task not in note["body"]
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == note["id"]
    assert record["responsible"] == {"task_id": None, "request_id": None, "backlog": False}


# --- task: follow-up acknowledgement after blocked ---


def _replies(board: Board, task: str) -> list[dict]:
    return [m for m in _outbox(board) if m.get("in_reply_to") == task]


def _handle_blocked(board: Board) -> list:
    return blocked.handle_blocked(board.ctx(), board.queue, allow_no_remote=True)


def _block_after_finish(board: Board, worker, reason: str = "needs a human") -> None:
    """``card finish --outcome blocked``, then the card is blocked on the board."""
    worker.finish(outcome="blocked", terminal=False)
    board.queue.block(worker.task_id, worker.run_id, reason)


def _blocked_task(board: Board, reason: str = "needs a human"):
    """A task whose card finished ``blocked`` and was blocked, published: the task is
    acknowledged ``blocked``. Returns (task, its card, the blocked note)."""
    task, worker = _finished_task_card(board)
    _block_after_finish(board, worker, reason)
    board.process(publish=local_hook)
    [first] = _replies(board, task)
    assert first["about"]["outcome"] == "blocked"
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == first["id"]
    return task, worker, first


def _start_repair(board: Board, action) -> object:
    assert action.action == blocked.REPAIRED and action.repair_request
    repair = board.admit_follow_up(action.repair_request)
    repair.start()
    repair.write_journal()
    return repair


def _blocked_task_with_repair_card(board: Board):
    """A blocked task whose card's handoff is published is repaired at once, never requeued
    (its rerun could not hand off). Returns (task, blocked note, started repair card)."""
    task, worker, first = _blocked_task(board)
    [action] = _handle_blocked(board)
    assert board.queue.get(worker.task_id).state == "blocked"  # not requeued
    key = f"repair-after-publish:{worker.task_id}"
    notes = [m for m in _outbox(board) if m.get("runtime_key") == key]
    assert len(notes) == 1 and notes[0]["kind"] == "note"
    repair = _start_repair(board, action)
    assert repair.task_id != worker.task_id
    assert _handle_blocked(board) == []  # repaired once
    return task, first, repair


def test_a_blocked_task_gets_one_follow_up_when_its_repair_card_finishes(board):
    task, first, repair = _blocked_task_with_repair_card(board)
    repair.finish()

    board.process(publish=local_hook)

    [_, follow_up] = _replies(board, task)
    assert follow_up["kind"] == "note" and follow_up["from"] == "runtime"
    assert follow_up["about"]["outcome"] == "done" and follow_up["about"]["follows"] == first["id"]
    assert follow_up["about"]["card"] == repair.task_id
    assert follow_up["runtime_key"] == f"ack-final:{task}"
    assert (follow_up["title"], follow_up["body"]) == ("Done: Scout", "Did things.")
    record = _delivery(board, task)
    assert record["acknowledged_by"] == first["id"] and record["final_ack_by"] == follow_up["id"]
    _assert_clean(board)

    handoff = repair.handoff()
    local_hook(board.canonical, handoff["merge_sha"], handoff)
    assert len(_replies(board, task)) == 2
    _assert_clean(board)


def test_a_card_requeued_before_its_blocked_handoff_publishes_sends_the_follow_up(board):
    """health may requeue the card before queue-keeper publishes its blocked handoff: the
    request is still open, so the same card hands off again."""
    task, worker = _finished_task_card(board)
    _block_after_finish(board, worker)
    [action] = _handle_blocked(board)
    assert action.action == blocked.REQUEUED
    worker.start()
    worker.write_journal()
    worker.finish()

    board.process(publish=local_hook)

    [first, follow_up] = _replies(board, task)
    assert first["about"]["outcome"] == "blocked"
    assert follow_up["about"] | {"merge_commit": None} == {
        "card": worker.task_id,
        "request": worker.request_id,
        "merge_commit": None,
        "outcome": "done",
        "follows": first["id"],
    }
    assert _delivery(board, task)["final_ack_by"] == follow_up["id"]


@pytest.mark.parametrize("running", [False, True])
def test_an_unblocked_tool_fault_card_whose_handoff_is_published_is_repaired(board, running):
    """D-26: a tool-fault card stays blocked until a human acts. Unblocked (Hermes ``ready``,
    soon ``running``), it could not hand off again: it is repaired and closed instead."""
    task, worker, first = _blocked_task(board, reason="tool-fault: softmax build: boom")
    [action] = _handle_blocked(board)
    assert action.action == blocked.TOOL_FAULT
    assert board.queue.get(worker.task_id).state == "blocked"
    board.queue.requeue(worker.task_id, "fixed the tool")  # the human's kanban unblock
    if running:
        worker.start()

    [action] = _handle_blocked(board)

    assert board.queue.get(worker.task_id).state == ("blocked" if running else "completed")
    assert _handle_blocked(board) == []
    repair = _start_repair(board, action)
    repair.finish()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["follows"] == first["id"]


class _QueueWith:
    """The real queue, with some methods replaced."""

    def __init__(self, queue, **replaced):
        self.queue = queue
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self.queue, name)


def _repair_requests(board: Board, task_id: str) -> list[dict]:
    return [r for r in _requests(board) if f"[repair-blocked:{task_id}]" in r["spec"]["why"]]


def test_an_unblocked_card_claimed_between_the_listing_and_the_drop_is_stopped(board):
    """The dispatcher claims an unblocked card within seconds. The listing said ``pending``,
    the drop finds it claimed: the rerun is stopped, and only then is the repair requested."""
    task, worker, first = _blocked_task(board, reason="tool-fault: softmax build: boom")
    _handle_blocked(board)
    board.queue.requeue(worker.task_id, "fixed the tool")  # the human's kanban unblock
    queue, repairs_at_stop = board.queue, []

    def drop(task_id, reason):
        worker.start()  # the dispatcher wins the race
        queue.drop(task_id, reason)

    def stop(task_id, reason):
        repairs_at_stop.extend(_repair_requests(board, task_id))
        return queue.stop(task_id, reason)

    board.queue = _QueueWith(queue, drop=drop, stop=stop)

    [action] = _handle_blocked(board)

    assert repairs_at_stop == []
    card = queue.get(worker.task_id)
    assert (card.state, card.block_reason) == ("blocked", blocked.RERUN_AFTER_PUBLISH)
    assert _handle_blocked(board) == []
    [request] = _repair_requests(board, worker.task_id)
    assert request["id"] == action.repair_request
    board.queue = queue
    repair = _start_repair(board, action)
    repair.finish()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["follows"] == first["id"]


def test_a_rerun_that_cannot_be_closed_gets_no_repair_request_until_it_is_closed(board):
    """The rerun and its repair card never both run: no close, no repair request."""
    _task, worker, _first = _blocked_task(board, reason="tool-fault: softmax build: boom")
    _handle_blocked(board)
    board.queue.requeue(worker.task_id, "fixed the tool")
    worker.start()
    queue = board.queue

    def stop(task_id, reason):
        raise RuntimeError("hermes kanban block: database is locked")

    board.queue = _QueueWith(queue, stop=stop)
    with pytest.raises(RuntimeError, match="database is locked"):
        _handle_blocked(board)
    assert _repair_requests(board, worker.task_id) == []
    assert queue.get(worker.task_id).state == "claimed"

    board.queue = queue
    [action] = _handle_blocked(board)

    assert queue.get(worker.task_id).state == "blocked"
    assert [r["id"] for r in _repair_requests(board, worker.task_id)] == [action.repair_request]


@pytest.mark.parametrize("closed_as", ["blocked", "completed"])
def test_a_crash_between_the_close_and_the_repair_request_is_finished_once(
    board, monkeypatch, closed_as
):
    """Stopped, the rerun is found ``blocked``; dropped, it is found ``completed``."""
    _task, worker, _first = _blocked_task(board, reason="tool-fault: softmax build: boom")
    _handle_blocked(board)
    board.queue.requeue(worker.task_id, "fixed the tool")
    if closed_as == "blocked":
        worker.start()
    repair = blocked._repair

    def crash(*args, **kwargs):
        raise Crash

    monkeypatch.setattr(blocked, "_repair", crash)
    with pytest.raises(RuntimeError, match="Crash"):
        _handle_blocked(board)
    assert board.queue.get(worker.task_id).state == closed_as
    assert _repair_requests(board, worker.task_id) == []
    monkeypatch.setattr(blocked, "_repair", repair)

    [action] = _handle_blocked(board)

    assert _handle_blocked(board) == []
    assert [r["id"] for r in _repair_requests(board, worker.task_id)] == [action.repair_request]


def test_a_rerun_the_dispatcher_never_claims_is_repaired_before_it_is_archived(board):
    """Hermes ``triage`` (also ``todo``, ``scheduled``): the drop archives the card and the
    listing then leaves it out, so its repair request is written before the drop."""
    _task, worker, _first = _blocked_task(board, reason="tool-fault: softmax build: boom")
    _handle_blocked(board)
    board.queue.requeue(worker.task_id, "fixed the tool")
    queue, repairs_at_drop = board.queue, []

    def list_cards(state=None):
        return [
            dataclasses.replace(card, board_status="triage") if card.state == "pending" else card
            for card in queue.list(state)
        ]

    def drop(task_id, reason):
        repairs_at_drop.extend(_repair_requests(board, task_id))
        queue.drop(task_id, reason)

    board.queue = _QueueWith(queue, list=list_cards, drop=drop)

    [action] = _handle_blocked(board)

    assert [r["id"] for r in repairs_at_drop] == [action.repair_request]
    assert queue.get(worker.task_id).state == "completed"


def test_a_repair_of_a_repair_sends_the_follow_up(board):
    task, first, repair = _blocked_task_with_repair_card(board)
    _block_after_finish(board, repair)
    board.process(publish=local_hook)
    assert len(_replies(board, task)) == 1
    [action] = _handle_blocked(board)
    second = _start_repair(board, action)
    second.finish()

    board.process(publish=local_hook)

    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["card"] == second.task_id
    assert follow_up["about"]["follows"] == first["id"]
    assert _delivery(board, task)["final_ack_by"] == follow_up["id"]


def test_a_repair_chain_stops_at_the_limit_with_one_help_request(board):
    task, _first, repair = _blocked_task_with_repair_card(board)
    _block_after_finish(board, repair)
    board.process(publish=local_hook)
    second = _start_repair(board, _handle_blocked(board)[0])
    _block_after_finish(board, second)
    board.process(publish=local_hook)
    requests_before = len(list((board.canonical / "cards" / "requests").glob("*.json")))

    [action] = _handle_blocked(board)

    assert action.action == blocked.CHAIN_LIMIT and action.repair_request is None
    assert _handle_blocked(board) == []
    assert len(list((board.canonical / "cards" / "requests").glob("*.json"))) == requests_before
    key = f"repair-limit:{second.task_id}"
    [help_request] = [m for m in _outbox(board) if m.get("runtime_key") == key]
    assert help_request["kind"] == "help_request" and help_request["expects_response"] is True
    assert help_request["priority"] == "P1"
    marker = board.committed(blocked.blocked_marker_path(board.canonical, second.task_id))
    assert marker["chain_limit"] is True and marker["repair_request"] is None
    assert board.queue.get(second.task_id).state == "blocked"
    assert len(_replies(board, task)) == 1


def test_a_rerun_of_a_card_whose_published_handoff_was_final_is_closed_without_repair(board):
    """``card finish --outcome done``, then the run ends without ``kanban_complete`` (Hermes
    blocks it): the work is published and over. Nothing is redone, also after an unblock."""
    task, worker = _finished_task_card(board)
    worker.finish(outcome="done", terminal=False)
    board.queue.block(worker.task_id, worker.run_id, "circuit breaker")
    board.process(publish=local_hook)
    requests = sorted((board.canonical / "cards" / "requests").glob("*.json"))

    assert _handle_blocked(board) == []
    assert board.queue.get(worker.task_id).state == "blocked"
    board.queue.requeue(worker.task_id, "a human unblocks it")
    assert _handle_blocked(board) == []

    assert board.queue.get(worker.task_id).state == "completed"  # the rerun was dropped
    assert sorted((board.canonical / "cards" / "requests").glob("*.json")) == requests
    assert not [m for m in _outbox(board) if m["kind"] != "note" or m.get("in_reply_to") != task]
    assert len(_replies(board, task)) == 1 and "final_ack_by" not in _delivery(board, task)


def test_a_running_rerun_that_already_handed_off_is_left_to_finish(board):
    """Requeued before its blocked handoff published; the rerun hands off ``done`` and, before
    its run ends, the blocked handoff publishes. The rerun is not stopped or repaired."""
    task, worker = _finished_task_card(board)
    _block_after_finish(board, worker)
    [action] = _handle_blocked(board)
    assert action.action == blocked.REQUEUED
    worker.start()
    worker.write_journal()
    worker.finish(terminal=False)
    board.process(publish=local_hook)
    assert worker.receipt()["state"] == "published"

    assert _handle_blocked(board) == []
    assert board.queue.get(worker.task_id).state == "claimed"

    worker.end_run()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["outcome"] == "done"


def test_a_repair_request_not_created_by_the_runtime_is_not_followed(board):
    task, worker, _first = _blocked_task(board)
    other = board.admit()  # a planner's request, named in a hand-written marker
    marker = blocked.blocked_marker_path(board.canonical, worker.task_id)
    forged = {"task_id": worker.task_id, "state": "repaired", "repair_request": other.request_id}
    write_json(marker, forged)
    gitops.commit_paths(board.canonical, [marker.relative_to(board.canonical).as_posix()], "marker")
    other.start()
    other.write_journal()
    other.finish()

    board.process(publish=local_hook)

    assert len(_replies(board, task)) == 1 and "final_ack_by" not in _delivery(board, task)


def test_a_task_first_acknowledged_done_gets_no_follow_up(board):
    task, worker = _finished_task_card(board)
    worker.finish()
    board.process(publish=local_hook)
    handoff = worker.handoff()

    local_hook(board.canonical, handoff["merge_sha"], handoff)

    [ack] = _replies(board, task)
    assert ack["about"]["outcome"] == "done" and "final_ack_by" not in _delivery(board, task)


def test_a_blocked_task_whose_repair_card_blocks_too_gets_no_follow_up(board):
    task, _first, repair = _blocked_task_with_repair_card(board)
    _block_after_finish(board, repair)

    board.process(publish=local_hook)

    assert len(_replies(board, task)) == 1 and "final_ack_by" not in _delivery(board, task)


def test_a_crash_between_the_follow_up_and_its_record_publishes_it_once(board, monkeypatch):
    task, _first, repair = _blocked_task_with_repair_card(board)
    repair.finish(outcome="failed")
    real = publisher._checkpoint

    def crash(name: str) -> None:
        if name == "final_ack_published":
            monkeypatch.setattr(publisher, "_checkpoint", real)
            raise Crash(name)

    monkeypatch.setattr(publisher, "_checkpoint", crash)
    with pytest.raises(Crash):
        board.process(publish=local_hook)
    assert "final_ack_by" not in _delivery(board, task)
    board.process(publish=local_hook)

    [final] = [m for m in _outbox(board) if m.get("runtime_key") == f"ack-final:{task}"]
    assert final["about"]["outcome"] == "failed"
    assert _delivery(board, task)["final_ack_by"] == final["id"]
    _assert_clean(board)


def test_an_unrelated_card_does_not_send_the_follow_up(board):
    task, _first, _repair = _blocked_task_with_repair_card(board)
    other = board.admit()
    other.start()
    other.write_journal()
    other.finish()

    board.process(publish=local_hook)

    assert len(_replies(board, task)) == 1 and "final_ack_by" not in _delivery(board, task)


# --- steer ---


def test_urgent_steer_gets_a_p0_card_and_must_be_acknowledged(board):
    steer = _send(board, "steer", text="Stop pushing mid lane.", urgency="now")
    _route(board)
    [request] = _requests(board)
    assert request["spec"]["priority"] == "P0" and request["spec"]["mode"] == "investigation"
    assert f"[steer:{steer}]" in request["spec"]["why"]

    worker = board.admit_follow_up(request["id"])
    _route(board)
    assert _card_file(board, worker.task_id)["responsible_inbound"] == [steer]
    worker.start()
    worker.write_journal()
    note = stage(
        Layout(worker.worktree), "note", "Stopped mid pushes", "Done.", [], in_reply_to=steer
    )
    record = worker.finish()
    assert record["responsible_inbound"] == [steer]

    [outcome] = board.process(publish=local_hook)

    assert outcome.state == "published"
    delivery = _delivery(board, steer)
    assert delivery["state"] == "acknowledged" and delivery["acknowledged_by"] == note
    published = {m["id"]: m for m in _outbox(board)}
    assert published[note]["card"] == worker.task_id  # the thread's card, for later replies
    _assert_clean(board)


def test_urgent_steer_with_a_topic_gets_a_planning_card(board):
    _send(board, "steer", text="Look at towers.", urgency="now", proposed_topic=TOPIC)
    _route(board)
    [request] = _requests(board)
    assert request["spec"]["mode"] == "planning" and request["spec"]["priority"] == "P0"


def test_urgent_message_is_read_only_in_a_second_briefing(board):
    steer = _send(board, "steer", text="Stop pushing mid lane.", urgency="now")
    _route(board)
    [request] = _requests(board)
    responsible = board.admit_follow_up(request["id"])
    other = board.admit()
    _route(board)

    mine = inbound_for_briefing(board.canonical, responsible.task_id)
    theirs = inbound_for_briefing(board.canonical, other.task_id)

    assert [m["id"] for m in mine] == [steer] and mine[0]["responsible_task"] == responsible.task_id
    assert [m["id"] for m in theirs] == [steer]
    assert theirs[0]["responsible_task"] == responsible.task_id
    spec = card_spec(_card_file(board, other.task_id))
    text = build_briefing(board.canonical, spec, other.task_id, BriefingInputs(inbound=theirs))
    assert f"handled by {responsible.task_id}" in text and "Stop pushing" not in text
    spec = card_spec(_card_file(board, responsible.task_id))
    text = build_briefing(board.canonical, spec, responsible.task_id, BriefingInputs(inbound=mine))
    assert "you must acknowledge this" in text and "Stop pushing mid lane." in text


# --- planning backlog ---


def test_proposed_topic_and_simultaneous_planning_sources_make_one_planning_card(board):
    urgent = _send(board, "steer", text="Plan towers now.", urgency="now", proposed_topic=TOPIC)
    later = _send(board, "steer", text="Also jungle.", urgency="next", proposed_topic=TOPIC)
    task = _send(
        board, "task", "ide", title="Replan", mode="planning", why="W", refs=[], priority="P2"
    )

    _route(board)

    [request] = _requests(board)
    assert request["spec"]["mode"] == "planning" and request["spec"]["created_by"] == urgent
    assert _delivery(board, urgent)["responsible"]["request_id"] == request["id"]
    for inbound_id in (later, task):
        assert _delivery(board, inbound_id)["responsible"] == {
            "task_id": None,
            "request_id": None,
            "backlog": True,
        }
        assert (board.ledger / "backlog" / f"{inbound_id}.json").exists()
    assert len(planning_backlog(board.canonical)) == 2

    planner = board.admit_follow_up(request["id"])
    _route(board)
    assert sorted(_card_file(board, planner.task_id)["responsible_inbound"]) == sorted(
        [urgent, later, task]
    )
    assert _delivery(board, later)["responsible"]["task_id"] == planner.task_id


def test_orphan_reply_goes_to_the_backlog_and_the_next_planning_briefing(board):
    question = _question(board)
    reply = _send(board, "reply", in_reply_to=question, text="No.")

    _route(board)

    record = _delivery(board, reply)
    assert record["responsible"]["backlog"] and record["thread"] == question
    assert any(reply in line for line in planning_backlog(board.canonical))

    planner = board.admit(mode="planning")
    _route(board)
    inbound = inbound_for_briefing(board.canonical, planner.task_id)
    assert [m["id"] for m in inbound] == [reply] and inbound[0]["responsible_task"] == (
        planner.task_id
    )
    spec = card_spec(_card_file(board, planner.task_id))
    backlog = planning_backlog(board.canonical, planner.task_id)
    assert backlog == []  # shown once, in full, as this card's inbound
    inputs = BriefingInputs(inbound=inbound, planning_backlog=backlog)
    text = build_briefing(board.canonical, spec, planner.task_id, inputs)
    assert "you must acknowledge this" in text and "No." in text
    assert text.count(reply) == 1


def test_reply_on_a_thread_with_an_open_card_goes_to_that_card(board):
    question = _question(board)
    steer = _send(board, "steer", text="Hold on this.", urgency="now", in_reply_to=question)
    _route(board)
    [request] = _requests(board)

    reply = _send(board, "reply", in_reply_to=question, text="Actually yes.")
    _route(board)

    assert _delivery(board, reply)["responsible"] == {
        "task_id": None,
        "request_id": request["id"],
        "backlog": False,
    }
    worker = board.admit_follow_up(request["id"])
    _route(board)
    assert sorted(_card_file(board, worker.task_id)["responsible_inbound"]) == sorted(
        [steer, reply]
    )


def test_a_card_that_publishes_without_acknowledging_returns_it_to_the_backlog(board):
    question = _question(board)
    steer = _send(board, "steer", text="Hold.", urgency="now", in_reply_to=question)
    _route(board)
    [request] = _requests(board)
    worker = board.admit_follow_up(request["id"])
    # The card file never lists the steer (e.g. it was claimed first), so finish passes.
    worker.start()
    worker.write_journal()
    worker.finish()
    board.process(publish=local_hook)
    assert _delivery(board, steer)["responsible"]["request_id"] == request["id"]

    _route(board)  # the request is published: the steer goes back to the backlog

    record = _delivery(board, steer)
    assert record["state"] == "routed"
    assert record["responsible"] == {"task_id": None, "request_id": None, "backlog": True}


# --- seen, late, status ---


def test_seen_is_recorded_per_consumer_and_needs_no_card(board):
    question = _question(board)
    by_preston = _send(board, "seen", ids=[question])
    _route(board)
    assert read_json(seen_path(board.canonical, "preston"))["ids"] == [question]
    assert not seen_path(board.canonical, "ide").exists()
    record = _delivery(board, by_preston)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] is None
    assert _requests(board) == [] and planning_backlog(board.canonical) == []


def test_late_reply_is_delivered_and_flagged(board):
    question = _question(board, expires="2020-01-01T00:00:00+00:00")
    reply = _send(board, "reply", in_reply_to=question, text="Late yes.")
    _route(board)
    assert _delivery(board, reply)["late"] is True
    status = derived_status(board.canonical, question)
    assert (status.state, status.late) == ("answered", True)


# --- crash safety ---


@pytest.mark.parametrize("step", ["received_written", "request_written", "routed_written"])
def test_rerun_after_a_crash_makes_no_duplicate_requests(board, monkeypatch, step):
    task = _send(board, "task", "ide", title="T", mode="scouting", why="W", refs=[], priority="P1")
    steer = _send(board, "steer", text="Now.", urgency="now")

    def crash(name: str) -> None:
        if name == step:
            raise Crash(name)

    monkeypatch.setattr(routing, "_checkpoint", crash)
    with pytest.raises(Crash):
        _route(board)
    monkeypatch.undo()
    _route(board)
    _route(board)

    markers = sorted(r["spec"]["why"].rsplit("[", 1)[-1] for r in _requests(board))
    assert markers == sorted([f"task:{task}]", f"steer:{steer}]"])
    assert len(list((board.canonical / "cards" / "requests").glob("*.json"))) == 2
    _assert_clean(board)


def test_rerun_after_a_crash_makes_no_duplicate_notes(board, monkeypatch):
    missing = "ar://hypothesis/H-01K6Z3V8QX0000000000000000"
    task = _send(
        board, "task", "ide", title="T", mode="scouting", why="W", refs=[missing], priority="P1"
    )

    def crash(name: str) -> None:
        if name == "note_published":
            raise Crash(name)

    monkeypatch.setattr(routing, "_checkpoint", crash)
    with pytest.raises(Crash):
        _route(board)
    monkeypatch.undo()
    _route(board)

    notes = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert len(notes) == 1 and _delivery(board, task)["acknowledged_by"] == notes[0]["id"]


def test_rerun_after_a_crash_in_task_acknowledgement_makes_no_duplicate_note(board, monkeypatch):
    task = _send(
        board, "task", "ide", title="Scout", mode="scouting", why="W", refs=[], priority="P1"
    )
    _route(board)
    [request] = _requests(board)
    worker = board.admit_follow_up(request["id"])
    worker.start()
    worker.write_journal()
    worker.finish()
    real = publisher._checkpoint

    def crash(name: str) -> None:
        if name == "ack_published":
            monkeypatch.setattr(publisher, "_checkpoint", real)
            raise Crash(name)

    monkeypatch.setattr(publisher, "_checkpoint", crash)
    with pytest.raises(Crash):
        board.process(publish=local_hook)
    board.process(publish=local_hook)

    notes = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert len(notes) == 1 and _delivery(board, task)["acknowledged_by"] == notes[0]["id"]


def _crash_at_note_then_rerun(board: Board, monkeypatch) -> None:
    def crash(name: str) -> None:
        if name == "note_published":
            raise Crash(name)

    monkeypatch.setattr(routing, "_checkpoint", crash)
    with pytest.raises(Crash):
        _route(board)
    monkeypatch.undo()
    _route(board)
    _route(board)


def test_rerun_after_a_crash_applies_a_control_pause_once(board, monkeypatch):
    """The note is published before the hold: a crash there leaves the hold unapplied, and
    the rerun applies it with the same note."""
    worker = board.admit()
    control = _send(board, "control", "coach:ply_x", action="pause")

    _crash_at_note_then_rerun(board, monkeypatch)

    [note] = [m for m in _outbox(board) if m.get("in_reply_to") == control]
    assert note["title"] == "Research paused at your request."
    hold = board.committed(board.ledger / "coach-hold.json")
    assert hold["tasks"] == {worker.task_id: worker.request_id}
    assert board.queue.get(worker.task_id).assignee is None
    record = _delivery(board, control)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == note["id"]
    holds = gitops.git(board.canonical, "log", "--format=%s", "--grep=^coach hold").splitlines()
    assert len(holds) == 1
    _assert_clean(board)


def test_rerun_after_a_crash_applies_a_control_resume_once(board, monkeypatch):
    worker = board.admit()
    _send(board, "control", "coach:ply_x", action="pause")
    _route(board)
    resume = _send(board, "control", "coach:ply_x", action="resume")

    _crash_at_note_then_rerun(board, monkeypatch)

    [note] = [m for m in _outbox(board) if m.get("in_reply_to") == resume]
    assert note["title"] == "Research resumed."  # built before the release, reused after
    assert board.committed(board.ledger / "coach-hold.json") is None
    assert board.queue.get(worker.task_id).assignee == worker.request_id
    assert _delivery(board, resume)["acknowledged_by"] == note["id"]
    _assert_clean(board)


def test_control_messages_apply_in_the_order_sent_not_id_order(board):
    """Direct writes whose ids sort opposite to their times: the pause (sent first) applies
    before the resume, so research ends not held."""
    worker = board.admit()
    inbox = board.canonical / "comms" / "inbox"
    for inbound_id, action, created in (
        ("ply_x:control:b", "pause", "2026-10-09T20:00:00Z"),
        ("ply_x:control:a", "resume", "2026-10-09T20:01:00+00:00"),
    ):
        write_json(inbox / f"{inbound_id}.json", {
            "id": inbound_id, "created": created, "author": "coach:ply_x",
            "kind": "control", "action": action,
        })  # fmt: skip

    _route(board)

    assert board.committed(board.ledger / "coach-hold.json") is None
    assert board.queue.get(worker.task_id).assignee == worker.request_id
    titles = {
        m["in_reply_to"]: m["title"] for m in _outbox(board) if m.get("in_reply_to")
    }
    assert titles == {
        "ply_x:control:b": "Research paused at your request.",
        "ply_x:control:a": "Research resumed.",
    }


def test_a_control_whose_created_does_not_parse_does_not_stall_routing(board):
    """Validation accepts a direct write by its ``received_at`` even when ``created`` is not a
    time. Ordering must not raise on it: every inbound would stall. It sorts by the time that
    parses; a pause sent before it still applies first."""
    worker = board.admit()
    inbox = board.canonical / "comms" / "inbox"
    write_json(inbox / "ply_x:control:a.json", {
        "id": "ply_x:control:a", "created": "2026-10-09T20:00:00Z", "author": "coach:ply_x",
        "kind": "control", "action": "pause",
    })  # fmt: skip
    write_json(inbox / "ply_x:control:0.json", {
        "id": "ply_x:control:0", "created": "yesterday", "received_at": "2026-10-09T20:01:00Z",
        "author": "coach:ply_x", "kind": "control", "action": "resume",
    })  # fmt: skip
    task = _send(board, "task", "ide", title="T", mode="scouting", why="W", refs=[], priority="P1")

    _route(board)

    assert _delivery(board, task)["state"] != "received"
    assert _delivery(board, "ply_x:control:a")["state"] == "acknowledged"
    assert _delivery(board, "ply_x:control:0")["state"] == "acknowledged"
    assert board.committed(board.ledger / "coach-hold.json") is None
    assert board.queue.get(worker.task_id).assignee == worker.request_id


def test_routing_order_sorts_a_record_with_no_parsable_time_last():
    records = {
        name: {"id": name, "state": "received", "kind": "control"} for name in ("c:0", "c:1")
    }
    inbox = {
        "c:0": {"id": "c:0", "created": "bad", "received_at": "also bad"},
        "c:1": {"id": "c:1", "created": "2026-10-09T20:00:00Z"},
    }
    assert [record["id"] for record in _routing_order(records, inbox)] == ["c:1", "c:0"]


# --- fix round 1 ---


def test_urgent_steer_with_topic_while_planning_is_owned(board):
    planner = board.admit(mode="planning")  # pending: owns planning
    steer = _send(board, "steer", text="Towers now.", urgency="now", proposed_topic=TOPIC)

    _route(board)

    [request] = _requests(board)
    assert request["spec"]["priority"] == "P0" and request["spec"]["mode"] == "investigation"
    assert f"[steer:{steer}]" in request["spec"]["why"]
    record = _delivery(board, steer)
    assert record["responsible"] == {
        "task_id": None,
        "request_id": request["id"],
        "backlog": False,
    }
    entry = board.committed(board.ledger / "backlog" / f"{steer}.json")
    assert entry["source"] == "proposed_topic" and entry["proposed_topic"] == TOPIC
    assert entry["taken_by"] == planner.task_id  # the pending planning card takes the topic
    [line] = planning_backlog(board.canonical, planner.task_id)
    assert "Towers" in line
    assert planning_backlog(board.canonical, "K-other") == []
    _assert_clean(board)


def test_a_read_only_cards_note_does_not_acknowledge(board):
    steer = _send(board, "steer", text="Stop.", urgency="now")
    _route(board)
    [request] = _requests(board)
    board.admit_follow_up(request["id"])
    bystander = board.admit()
    bystander.start()
    bystander.write_journal()
    stage(Layout(bystander.worktree), "note", "Seen it", "Ok.", [], in_reply_to=steer)
    bystander.finish()

    board.process(publish=local_hook)

    assert any(m.get("in_reply_to") == steer for m in _outbox(board))
    assert _delivery(board, steer)["state"] == "routed"


def test_backlog_task_acknowledgement_does_not_claim_the_card_was_created_for_it(board):
    planner = board.admit(mode="planning")  # owns planning, so the task goes to the backlog
    task = _send(
        board, "task", "ide", title="Replan", mode="planning", why="W", refs=[], priority="P2"
    )
    _route(board)
    assert _delivery(board, task)["responsible"] == {
        "task_id": planner.task_id,
        "request_id": planner.request_id,
        "backlog": True,
    }
    planner.start()
    planner.write_journal()
    planner.finish()

    board.process(publish=local_hook)

    [ack] = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert "planning card that was already open took this task on" in ack["body"]


def test_reply_on_a_thread_whose_card_is_claimed_goes_to_the_backlog(board):
    question = _question(board)
    _send(board, "steer", text="Hold on this.", urgency="now", in_reply_to=question)
    _route(board)
    [request] = _requests(board)
    worker = board.admit_follow_up(request["id"])
    _route(board)
    worker.start()  # claimed: it would never see a new message

    reply = _send(board, "reply", in_reply_to=question, text="Actually yes.")
    _route(board)

    assert _delivery(board, reply)["responsible"] == {
        "task_id": None,
        "request_id": None,
        "backlog": True,
    }
    assert reply not in _card_file(board, worker.task_id)["responsible_inbound"]


def test_reply_routes_to_an_admitted_pending_card_found_by_outbox_card(board):
    card = board.admit()
    question = _question(board, card=card.task_id, thread="credits")
    reply = _send(board, "reply", in_reply_to=question, text="Yes.")

    _route(board)

    assert _delivery(board, reply)["responsible"] == {
        "task_id": card.task_id,
        "request_id": card.request_id,
        "backlog": False,
    }
    assert _card_file(board, card.task_id)["responsible_inbound"] == [reply]


def test_task_rejected_by_admission_gets_a_runtime_note_with_the_reason(board):
    task = _send(board, "task", "ide", title="T", mode="scouting", why="W", refs=[], priority="P1")
    _route(board)
    [request] = _requests(board)
    receipt = {
        "request_id": request["id"],
        "state": "rejected",
        "mode": "scouting",
        "reason": "unresolved ref ar://claim/C-gone: not found",
        "history": [{"state": "rejected", "at": "2026-10-07T00:00:00+00:00"}],
    }
    path = board.receipt_path(request["id"])
    write_json(path, receipt)
    gitops.commit_paths(board.canonical, [path.relative_to(board.canonical).as_posix()], "reject")

    _route(board)

    [note] = [m for m in _outbox(board) if m.get("in_reply_to") == task]
    assert note["from"] == "runtime" and note["title"] == "Not started: T"
    assert "ar://claim/C-gone" in note["body"] and "references that exist" in note["body"]
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == note["id"]
    _assert_clean(board)


# --- Task 3.9: release taken topics; planning requests in the backlog ---


def _drop_receipt(board: Board, worker) -> None:
    receipt = board.committed(board.receipt_path(worker.request_id))
    receipt["state"] = "dropped"
    receipt["history"].append({"state": "dropped", "at": "2026-10-07T01:00:00+00:00"})
    write_json(board.receipt_path(worker.request_id), receipt)
    gitops.commit_paths(
        board.canonical,
        [board.receipt_path(worker.request_id).relative_to(board.canonical).as_posix()],
        "drop",
    )


def test_a_taken_topic_is_released_when_its_planning_card_is_dropped(board):
    planner = board.admit(mode="planning")
    steer = _send(board, "steer", text="Towers now.", urgency="now", proposed_topic=TOPIC)
    _route(board)
    entry_path = board.ledger / "backlog" / f"{steer}.json"
    assert board.committed(entry_path)["taken_by"] == planner.task_id
    _drop_receipt(board, planner)
    board.queue.drop(planner.task_id, "dropped by planning")

    _route(board)

    assert board.committed(entry_path)["taken_by"] is None
    assert any("Towers" in line for line in planning_backlog(board.canonical, "K-next"))
    _assert_clean(board)


def test_a_planning_request_in_the_backlog_is_shown_to_the_next_planning_card(board):
    from autoresearch.queue.card import CardSpec

    spec = CardSpec("Q-01K00000000000000000000000", "Plan jungle", "planning", "Jungle.", [],
                    "planner", "P2")  # fmt: skip
    with canonical_lock(board.canonical):
        path = routing.backlog_request(board.canonical, spec, "planning is owned")
        gitops.commit_paths(board.canonical, [path.relative_to(board.canonical).as_posix()], "b")
    assert any("Plan jungle" in line for line in planning_backlog(board.canonical))

    planner = board.admit(mode="planning")
    _route(board)

    assert board.committed(path)["taken_by"] == planner.task_id
    assert any("Plan jungle" in line for line in planning_backlog(board.canonical, planner.task_id))
    assert planning_backlog(board.canonical, "K-other") == []


# --- final fix round ---


def test_a_hold_applied_while_health_requeues_a_blocked_card_still_holds_it(board, monkeypatch):
    """Health runs in its own process. A coach hold applied (by queue-keeper routing, under the
    canonical lock) between health's hold check and its requeue must not leave the requeued
    card pending and assigned, so dispatchable, under the hold."""
    import threading

    from autoresearch.runtime import pause

    worker = board.admit()
    worker.start()
    board.queue.block(worker.task_id, worker.run_id, "circuit breaker")
    real_is_held = blocked.is_held
    holders = []

    def is_held_then_hold_applies(ledger):
        held = real_is_held(ledger)
        if not holders:

            def apply_hold():
                with canonical_lock(board.canonical):
                    pause.hold(board.queue, board.ledger, "coach:ply_x")

            holders.append(threading.Thread(target=apply_hold))
            holders[0].start()
            holders[0].join(timeout=2)  # finishes unless health holds the canonical lock
        return held

    monkeypatch.setattr(blocked, "is_held", is_held_then_hold_applies)

    _handle_blocked(board)
    holders[0].join()

    assert board.committed(board.ledger / "coach-hold.json") is not None
    card = board.queue.get(worker.task_id)
    assert not (card.state == "pending" and card.assignee is not None)


def test_a_completed_rerun_of_a_card_whose_blocked_handoff_was_published_is_repaired(board):
    """A human unblocks a card whose ``blocked`` handoff is published; the rerun's
    ``card finish`` is refused and it completes between two runs of the blocked-card step.
    It is repaired once, and the repair's handoff completes the coach's task."""
    task, worker, first = _blocked_task(board, reason="tool-fault: softmax build: boom")
    [action] = _handle_blocked(board)
    assert action.action == blocked.TOOL_FAULT
    board.queue.requeue(worker.task_id, "fixed the tool")  # the human's kanban unblock
    worker.start()
    worker.end_run("done")  # refused card finish, then kanban_complete
    assert board.queue.get(worker.task_id).state == "completed"

    [action] = _handle_blocked(board)

    assert _handle_blocked(board) == []  # the marker prevents a second repair
    marker = board.committed(blocked.blocked_marker_path(board.canonical, worker.task_id))
    assert marker["state"] == blocked.REPAIRED
    assert marker["repair_request"] == action.repair_request
    repair = _start_repair(board, action)
    repair.finish()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["follows"] == first["id"]


def test_a_completed_card_whose_only_run_handed_off_blocked_is_not_repaired_again(board):
    """No rerun: the card's last run is the one whose handoff is published."""
    _task, worker = _finished_task_card(board)
    worker.finish(outcome="blocked")  # the run ends completed, not blocked
    board.process(publish=local_hook)
    assert board.queue.get(worker.task_id).state == "completed"

    assert _handle_blocked(board) == []


def test_a_crash_after_the_repair_request_keeps_its_linkage_and_notifies_once(board, monkeypatch):
    """The repair request and its parent's marker (``repair_request``) are one commit, before
    admission can see the request; the notice is published after it and tracked on its own.
    A crash at the notice leaves the linkage durable: the repair still sends the follow-up,
    and the next run publishes the notice once."""
    task, worker, first = _blocked_task(board)
    real_publish = blocked.publish_message

    def crash(*args, **kwargs):
        raise Crash("publish")

    monkeypatch.setattr(blocked, "publish_message", crash)
    with pytest.raises(RuntimeError):
        _handle_blocked(board)
    monkeypatch.setattr(blocked, "publish_message", real_publish)

    marker = board.committed(blocked.blocked_marker_path(board.canonical, worker.task_id))
    assert marker["state"] == blocked.REPAIRED and marker["notified"] is False
    request_id = marker["repair_request"]
    assert board.follow_up(request_id)["spec"]["created_by"] == "runtime"
    request_commit = gitops.git(
        board.canonical, "log", "-1", "--format=%H", "--", f"cards/requests/{request_id}.json"
    ).strip()
    marker_path = blocked.blocked_marker_path(board.canonical, worker.task_id)
    marker_commit = gitops.git(
        board.canonical, "log", "-1", "--format=%H", "--",
        marker_path.relative_to(board.canonical).as_posix(),
    ).strip()  # fmt: skip
    assert request_commit == marker_commit

    repair = board.admit_follow_up(request_id)  # admission runs before health's next run
    repair.start()
    repair.write_journal()
    repair.finish()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["follows"] == first["id"]

    assert _handle_blocked(board) == []
    assert _handle_blocked(board) == []
    key = f"repair-after-publish:{worker.task_id}"
    assert len([m for m in _outbox(board) if m.get("runtime_key") == key]) == 1
    marker = board.committed(blocked.blocked_marker_path(board.canonical, worker.task_id))
    assert marker["notified"] is True


def test_a_malformed_blocked_marker_does_not_break_repairs_or_follow_ups(board):
    """Only the runtime writes these markers, but one that is not an object, or names a
    ``repair_request`` that is not a string, must not break every repair and every final
    acknowledgement."""
    markers = blocked.blocked_marker_path(board.canonical, "x").parent
    markers.mkdir(parents=True, exist_ok=True)
    (markers / "K-list.json").write_text("[1, 2]\n")
    write_json(markers / "K-odd.json", {"task_id": "K-odd", "repair_request": ["Q-x"]})
    gitops.commit_paths(
        board.canonical,
        [p.relative_to(board.canonical).as_posix() for p in markers.glob("K-*.json")],
        "malformed markers",
    )

    task, first, repair = _blocked_task_with_repair_card(board)
    own = blocked.blocked_marker_path(board.canonical, repair.task_id)  # read by publisher
    own.write_text('"repaired"\n')
    gitops.commit_paths(board.canonical, [own.relative_to(board.canonical).as_posix()], "bad")
    repair.finish()
    board.process(publish=local_hook)

    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["follows"] == first["id"]


def test_a_published_receipt_with_an_unreadable_handoff_is_logged_not_repaired(board, caplog):
    """Task 10b N8: the outcome is ``unknown`` (a corrupt ledger). Nothing is redone, and it
    is not silent: one log line per run, no help request."""
    _task, worker, _first = _blocked_task(board)
    gitops.git(board.canonical, "rm", "-q", worker.handoff_path().as_posix())
    gitops.git(board.canonical, "commit", "-q", "-m", "lose the handoff")
    outbox_before = len(_outbox(board))

    with caplog.at_level("WARNING", logger="autoresearch.runtime.blocked"):
        assert _handle_blocked(board) == []

    [line] = [r.getMessage() for r in caplog.records if worker.task_id in r.getMessage()]
    assert "unknown" in line
    assert len(_outbox(board)) == outbox_before
    assert board.queue.get(worker.task_id).state == "blocked"


class _LookupFails:
    """The board, except that ``get`` of one card fails (``process_handoffs`` then skips it)."""

    def __init__(self, queue, task_id: str):
        self._queue, self._task_id = queue, task_id

    def get(self, task_id: str):
        if task_id == self._task_id:
            raise LookupError(task_id)
        return self._queue.get(task_id)

    def __getattr__(self, name):
        return getattr(self._queue, name)


def test_a_repair_published_before_the_blocked_handoff_still_sends_the_follow_up(board):
    """Codex round 2: health repairs a card blocked twice while its ``blocked`` handoff is
    unpublished; the original's board lookup fails, so the repair publishes ``done`` first.
    When the original's ``blocked`` acknowledgement is recorded later, the follow-up is sent
    from the repair's published outcome, once."""
    task, worker = _finished_task_card(board)
    worker.finish(outcome="blocked", terminal=False)
    board.queue.block(worker.task_id, worker.run_id, "circuit breaker")
    [action] = _handle_blocked(board)
    assert action.action == blocked.REQUEUED
    worker.start()
    board.queue.block(worker.task_id, worker.run_id, "circuit breaker")
    [action] = _handle_blocked(board)
    repair = _start_repair(board, action)
    repair.finish()

    board.process(publish=local_hook, queue=_LookupFails(board.queue, worker.task_id))
    assert repair.receipt()["state"] == "published" and _replies(board, task) == []

    board.process(publish=local_hook)

    [first, follow_up] = _replies(board, task)
    assert first["about"]["outcome"] == "blocked"
    assert follow_up["about"]["outcome"] == "done" and follow_up["about"]["follows"] == first["id"]
    assert follow_up["about"]["card"] == repair.task_id
    assert follow_up["runtime_key"] == f"ack-final:{task}"
    assert _delivery(board, task)["final_ack_by"] == follow_up["id"]
    board.process(publish=local_hook)
    assert len(_replies(board, task)) == 2
    _assert_clean(board)


def _finish_recovery(board: Board, request_id: str, task: str, first: dict) -> None:
    """Admit and finish the runtime recovery card ``request_id``; its ``done`` handoff sends
    the coach's follow-up, once."""
    recovery = board.admit_follow_up(request_id)
    recovery.start()
    recovery.write_journal()
    recovery.finish()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["outcome"] == "done" and follow_up["about"]["follows"] == first["id"]
    assert follow_up["about"]["card"] == recovery.task_id
    assert _delivery(board, task)["final_ack_by"] == follow_up["id"]
    board.process(publish=local_hook)
    assert len(_replies(board, task)) == 2
    _assert_clean(board)


def test_a_repair_whose_handoff_merge_conflicts_is_finished_by_its_reconcile_card(board):
    """Codex round 3: the repair's handoff conflicts and goes to ``reconcile``; the reconcile
    card's ``done`` completes the coach's task."""
    task, first, repair = _blocked_task_with_repair_card(board)
    other = board.admit()
    other.start()
    (other.worktree / "strategy" / "notes.md").write_text("# Notes\n\nCurrent plan: push.\n")
    (repair.worktree / "strategy" / "notes.md").write_text("# Notes\n\nCurrent plan: defend.\n")
    other.write_journal()
    other.finish()
    board.process(publish=local_hook)  # canonical moves on first
    repair.finish()
    board.process(publish=local_hook)
    record = repair.handoff()
    assert record["state"] == "reconcile" and len(_replies(board, task)) == 1

    _finish_recovery(board, record["follow_up_request"], task, first)


def test_a_repair_that_leaves_no_handoff_is_finished_by_its_missing_handoff_repair(board):
    from autoresearch.runtime.merge import find_orphan_done

    task, first, repair = _blocked_task_with_repair_card(board)
    repair.end_run("done")  # completed without card finish
    [request_id] = find_orphan_done(board.queue, board.ledger)
    assert len(_replies(board, task)) == 1

    _finish_recovery(board, request_id, task, first)


def test_a_repair_receipt_naming_an_unpublished_newer_run_is_not_taken_as_finished(board):
    """Codex round 4: the repair is requeued before its own ``blocked`` handoff publishes; its
    rerun hands off ``done`` (the receipt's ``handed_off`` moves to it) but is still running.
    Publishing the older run closes the receipt. The sweep must not take the unpublished
    newer handoff as finished (it has no ``merge_sha``); the follow-up waits for it."""
    task, worker = _finished_task_card(board)
    worker.finish(outcome="blocked", terminal=False)
    board.queue.block(worker.task_id, worker.run_id, "circuit breaker")
    _handle_blocked(board)  # requeued
    worker.start()
    board.queue.block(worker.task_id, worker.run_id, "circuit breaker")
    [action] = _handle_blocked(board)
    repair = _start_repair(board, action)
    repair.finish(outcome="blocked", terminal=False)
    board.queue.block(repair.task_id, repair.run_id, "circuit breaker")
    assert [a.action for a in _handle_blocked(board)] == [blocked.REQUEUED]
    repair.start()
    repair.write_journal()
    repair.finish(terminal=False)  # done, still running: handed_off now names this run
    lookup_fails = _LookupFails(board.queue, worker.task_id)
    board.process(publish=local_hook, queue=lookup_fails)
    assert repair.receipt()["state"] == "published"

    board.process(publish=local_hook)  # the original's blocked handoff publishes

    [first] = _replies(board, task)
    assert first["about"]["outcome"] == "blocked"
    repair.end_run()
    board.process(publish=local_hook)
    [_, follow_up] = _replies(board, task)
    assert follow_up["about"]["outcome"] == "done" and follow_up["about"]["card"] == repair.task_id
    assert _delivery(board, task)["final_ack_by"] == follow_up["id"]
    _assert_clean(board)


def test_a_follow_up_candidate_missing_a_field_is_skipped_with_a_log_line(
    board, monkeypatch, caplog
):
    """The follow-up builder never raises on a candidate handoff without ``merge_sha``: it
    would stop every acknowledgement of the pass."""
    task, _worker, _first = _blocked_task(board)
    incomplete = {"card": "K-x", "run_id": "r-x", "request_id": "Q-x", "outcome": "done"}
    monkeypatch.setattr(publisher, "_published_repair_outcome", lambda *args: incomplete)
    other = board.admit()
    other.start()
    other.write_journal()
    other.finish()

    with caplog.at_level("WARNING", logger="autoresearch.comms.publisher"):
        board.process(publish=local_hook)

    assert other.receipt()["state"] == "published"
    assert len(_replies(board, task)) == 1
    assert any("merge_sha" in r.getMessage() and task in r.getMessage() for r in caplog.records)
