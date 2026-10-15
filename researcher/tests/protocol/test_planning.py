"""Planning cadence with a single planning owner (Task 3.8, Overview C11, System Design §10).
Real git canonical, FileQueue and simulated workers (``tests/support/board.py``)."""

import datetime

import pytest
from support.board import Board

from ide_research_core.comms import delivery
from ide_research_core.io import gitops
from ide_research_core.io.docs import write_json
from ide_research_core.runtime.planning import planning_request

NOW = datetime.datetime(2026, 10, 7, 13, 5, tzinfo=datetime.UTC)
LATER = NOW + datetime.timedelta(hours=2)


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


def _request(board: Board, now=NOW, **kwargs):
    return planning_request(board.ctx(), board.queue, now, **kwargs)


def _committed_request(board: Board, request_id: str) -> dict:
    return board.follow_up(request_id)


def test_first_request_in_a_slot_is_committed(board):
    spec = _request(board)

    assert spec is not None and spec.mode == "planning" and spec.created_by == "runtime"
    assert "[planning:2026-10-07-12]" in spec.why  # the 2-hour slot that holds 13:05
    assert "Planning backlog: 0 open entries" in spec.why
    request = _committed_request(board, spec.request_id)
    assert request["op"] == "create" and request["spec"]["mode"] == "planning"
    assert gitops.git(board.canonical, "status", "--porcelain") == ""


def test_same_slot_requests_once(board):
    assert _request(board) is not None
    assert _request(board, now=NOW + datetime.timedelta(minutes=40)) is None


def test_cadence_is_configurable(board):
    spec = _request(board, every=datetime.timedelta(hours=1))
    assert "[planning:2026-10-07-13]" in spec.why


def test_backlog_count_in_why(board):
    write_json(
        delivery.backlog_path(board.canonical, "I-01K6Z3V8QX0000000000000000"),
        {
            "id": "I-01K6Z3V8QX0000000000000000",
            "source": "proposed_topic",
            "kind": "steer",
            "author": "preston",
            "summary": "Proposed topic: scouting",
            "proposed_topic": {"title": "Scout"},
            "taken_by": None,
            "created_at": "2026-10-07T00:00:00+00:00",
        },
    )
    gitops.commit_all(board.canonical, "backlog")
    assert "Planning backlog: 1 open entries" in _request(board).why


def test_open_topics_are_refs(board):
    topic = board.store.create(
        "topic",
        {
            "title": "Last hits",
            "objective": "More last hits.",
            "metrics": [],
            "guardrails": [],
            "origin": {"kind": "human"},
            "priority": 1,
            "status": "open",
        },
    )
    closed = board.store.create(
        "topic",
        {
            "title": "Old",
            "objective": "Old work.",
            "metrics": [],
            "guardrails": [],
            "origin": {"kind": "agent"},
            "priority": 3,
            "status": "closed",
        },
    )
    gitops.commit_all(board.canonical, "topics")
    spec = _request(board)
    assert f"ar://topic/{topic}" in spec.refs and f"ar://topic/{closed}" not in spec.refs


def test_not_requested_while_planning_is_owned_until_published(board):
    first = _request(board)
    assert _request(board, now=LATER) is None  # requested, not admitted yet

    worker = board.admit_follow_up(first.request_id)
    assert _request(board, now=LATER) is None  # pending

    worker.start()
    assert _request(board, now=LATER) is None  # claimed

    worker.write_journal()
    worker.finish()
    assert board.queue.get(worker.task_id).state == "completed"
    assert _request(board, now=LATER) is None  # finished but unpublished

    [outcome] = board.process()
    assert outcome.state == "published"
    assert _request(board, now=LATER) is not None


def test_queue_growth_requests_planning_outside_the_cadence(board):
    assert _request(board) is not None
    board.admit_follow_up(_request_ids(board)[0])
    assert _request(board, growth=True) is None  # still owned

    (board.tmp_path / "other").mkdir()
    other = Board(board.tmp_path / "other")  # a fresh workspace: planning is free there
    spec = planning_request(other.ctx(), other.queue, NOW, growth=True)
    assert "[planning:growth:2026-10-07]" in spec.why and "queue growth" in spec.why.lower()


def _request_ids(board: Board) -> list[str]:
    return sorted(path.stem for path in (board.canonical / "cards" / "requests").glob("Q-*.json"))


def test_crash_before_commit_reuses_the_written_request(board, monkeypatch):
    from ide_research_core.runtime import planning

    def crash(*args, **kwargs):
        raise RuntimeError("crash before commit")

    monkeypatch.setattr(planning, "commit", crash)
    with pytest.raises(RuntimeError):
        _request(board)
    monkeypatch.undo()
    spec = _request(board)
    assert spec is not None and _request_ids(board) == [spec.request_id]
