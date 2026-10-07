"""Inbound routing, acknowledgement and delivery state (Task 3.7, Overview C11): real git
canonical, FileQueue and simulated workers (``tests/support/board.py``)."""

import functools
import json

import jsonschema
import pytest
from support.board import Board

from autoresearch.comms import publisher
from autoresearch.comms.inbound import derived_status, submit_inbound
from autoresearch.comms.publisher import publish_hook, publish_runtime
from autoresearch.comms.reader import read_since, seen_path
from autoresearch.comms.stage import stage
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.requests import load_schema
from autoresearch.runtime import routing
from autoresearch.runtime.briefing import BriefingInputs, build_briefing
from autoresearch.runtime.handoff import card_spec
from autoresearch.runtime.ledger import canonical_lock
from autoresearch.runtime.routing import inbound_for_briefing, planning_backlog, route_all

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
    """Card requests committed at canonical HEAD."""
    listing = gitops.git(board.canonical, "ls-tree", "--name-only", "HEAD", "cards/requests/")
    return [json.loads(gitops.show(board.canonical, "HEAD", path)) for path in listing.split()]


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
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == ack["id"]
    _assert_clean(board)
    board.process(publish=local_hook)  # nothing left: no second acknowledgement
    assert len([m for m in _outbox(board) if m.get("in_reply_to") == task]) == 1


def test_invalid_task_gets_a_runtime_note_with_its_errors(board):
    missing = "ar://hypothesis/H-01K6Z3V8QX0000000000000000"
    task = _send(
        board, "task", "ide", title="T", mode="scouting", why="W", refs=[missing], priority="P1"
    )

    _route(board)

    assert _requests(board) == []
    [note] = _outbox(board)
    assert note["kind"] == "note" and note["in_reply_to"] == task and note["from"] == "runtime"
    assert missing in note["body"]
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == note["id"]
    assert record["responsible"] == {"task_id": None, "request_id": None, "backlog": False}


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
    assert "from the planning backlog" in ack["body"] and "created for" not in ack["body"]


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
    assert note["from"] == "runtime" and "unresolved ref ar://claim/C-gone" in note["body"]
    record = _delivery(board, task)
    assert record["state"] == "acknowledged" and record["acknowledged_by"] == note["id"]
    _assert_clean(board)
