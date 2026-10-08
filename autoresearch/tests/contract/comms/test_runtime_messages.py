"""Every message the runtime publishes says in plain words what happened (doctrine/
communication.md), with no ids or commits in its title or body (``comms.runtime_messages``).

The Softmax IDE bridge shows a runtime acknowledgement as the IDE task's outcome text, so these
titles and bodies reach humans verbatim. Ids go into ``refs`` and the additive ``about`` field.
"""

import inspect
import re
from pathlib import Path

import pytest
from support.board import Board

from autoresearch.comms import runtime_messages as rm
from autoresearch.comms.publisher import publish_message
from autoresearch.comms.reader import read_since
from autoresearch.runtime.ledger import canonical_lock

SRC = Path(rm.__file__).resolve().parents[1]
INBOUND = "I-01M4EJ7ABV097KEMKSBV76N9T9"
BRIDGE_TASK = "ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1:task:tsk_42"
REQUEST = "Q-01M4EJ9RQMHVH6XK3N4T760MNB"
CARD = "t_6c0a018c"
FILE_CARD = "K-01M4EJ9RQMHVH6XK3N4T760MNC"
MESSAGE = "M-01M4EJ9RQMHVH6XK3N4T760MND"
PROMOTION = "P-01M4EJ9RQMHVH6XK3N4T760MNE"
SHA = "f5e50b4909eb1c45d6a113fa930a7a37c14a2564"
OTHER_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
IDS = (INBOUND, BRIDGE_TASK, REQUEST, CARD, FILE_CARD, MESSAGE, PROMOTION, SHA, OTHER_SHA)
# Runtime ids (inbound, request, message, file-queue card), Kanban task ids, commits.
OPAQUE = re.compile(r"\b[IQMKP]-[0-9A-HJKMNP-TV-Z]{26}\b|\bt_[0-9a-f]{4,}\b|\b[0-9a-f]{40}\b")
HYPOTHESIS_REF = {"uri": "ar://hypothesis/H-01M4EJ9RQMHVH6XK3N4T760MNF", "rev": SHA}
ACK_ABOUT = {"card": CARD, "request": REQUEST, "merge_commit": SHA, "outcome": "done"}

ACK_CASES = [  # outcome, summary, titles of the card's messages, backlog
    ("done", "andre opens with two towers and rushes at minute 3.", ["Two-tower rush"], False),
    ("done", None, [], True),
    ("partial", None, ["A", "B", "C", "D"], False),
    ("dropped", None, [], False),
    ("failed", "The replay files are missing.", [], False),
    ("blocked", None, [], False),
]
LOST = f"board rebuild could not re-admit {REQUEST}: cards/requests/{REQUEST}.json is not committed"
SINCE = "2026-10-08T10:00:00+00:00"
OPEN_PROPOSAL = {
    "id": MESSAGE,
    "title": "Skill proposal: replay-diff",
    "body": "Reply adopt, reject, or revise.",
    "created": "2026-10-07T10:00:00+00:00",
    "refs": [{"uri": "ar://skill-proposal/replay-diff", "rev": SHA}],
}

# Arguments for each builder, every id-carrying argument filled with an id.
FIXTURES = {
    "task_acknowledgement": [
        {
            "task_title": "Scout andre's opening",
            "outcome": outcome,
            "summary": summary,
            "sent_titles": sent,
            "backlog": backlog,
            "has_journal": True,
            "refs": [HYPOTHESIS_REF],
            "about": ACK_ABOUT | {"outcome": outcome},
        }
        for outcome, summary, sent, backlog in ACK_CASES
    ],
    "task_not_started": [
        {"task_title": "Profile andre", "problems": ["mode: must be one of scouting, planning"]},
        {"task_title": "", "problems": ["title: required text"]},
    ],
    "task_not_admitted": [
        {"task_title": "Profile andre", "reason": "unresolved ref ar://claim/C-gone: not found"},
        {"task_title": "Profile andre", "reason": LOST},
        {"task_title": "Profile andre", "reason": "something new"},
    ],
    "inbox_write_rejected": [{"problems": ["not a regular file (a symlink or another type)"]}],
    "health_problem": [{"kind": "disk", "problem": "only 12 MB free", "day": "2026-10-08"}],
    "card_blocked_twice": [
        {
            "card_title": "Scout andre",
            "task_id": CARD,
            "request_id": REQUEST,
            "repair_request": repair,
            "refs": [],
        }
        for repair in (REQUEST, None)
    ],
    "card_tool_fault": [
        {
            "card_title": "Scout andre",
            "reason": "tool-fault: softmax build: FileNotFoundError",
            "task_id": CARD,
            "request_id": REQUEST,
            "run_id": "7",
            "refs": [],
        }
    ],
    "signal_persists": [
        {
            "code": "stale_card",
            "entity": CARD,
            "detail": "the card “Scout andre” has been pending 4 days",
            "first_seen": SINCE,
            "iterations": 3,
        }
    ],
    "agent_paused": [{"reasons": "identity: wrong player", "paused_at": SINCE}],
    "agent_resumed": [{"count": 2}],
    "staged_not_published": [
        {"message_id": MESSAGE, "staged_title": title, "problems": ["kind: bad"]}
        for title in ("Towers matter", None)
    ],
    "push_failing": [
        {"remote": "origin", "failures": 3, "local_head": SHA, "remote_head": OTHER_SHA}
    ],
    "step_failing": [
        {
            "step": "kb",
            "runs": 3,
            "since": SINCE,
            "error": "ValueError: bad claim",
            "log_path": ".supervisor/runtime/keeper.log",
        }
    ],
    "promotion_stuck": [
        {
            "promotion_id": PROMOTION,
            "recovery_requests": [REQUEST],
            "champion_path": "strategy/STRATEGY.md",
            "submission_id": "sub_1",
            "policy_version_id": "pv_1",
            "candidate_ref": f"card/{REQUEST}",
            "candidate_sha": SHA,
            "refs": [{"uri": "ar://experiment/E-01M4EJ9RQMHVH6XK3N4T760MNG"}],
        }
    ],
    "request_lost_in_rebuild": [{"request_id": REQUEST, "mode": "investigation"}],
    "skill_proposal": [
        {"name": "replay-diff", "tree_hash": "0123456789ab", "directory": "sp", "rev": SHA}
    ],
    "proposal_still_open": [{"message": OPEN_PROPOSAL, "rev": SHA}],
}


def _builders() -> dict:
    return {
        name: function
        for name, function in inspect.getmembers(rm, inspect.isfunction)
        if function.__module__ == rm.__name__
        and inspect.signature(function).return_annotation is rm.RuntimeMessage
    }


def test_every_builder_has_a_fixture():
    assert set(_builders()) == set(FIXTURES)


def test_runtime_code_publishes_only_built_messages():
    """``publish_runtime`` takes free text; runtime code publishes ``RuntimeMessage``s only."""
    callers = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path.name != "publisher.py" and "publish_runtime(" in path.read_text(encoding="utf-8")
    ]
    assert callers == []


@pytest.mark.parametrize(
    "name,kwargs",
    [(name, kwargs) for name, cases in FIXTURES.items() for kwargs in cases],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_published_runtime_message_is_plain(tmp_path, name, kwargs):
    board = Board(tmp_path)
    message = _builders()[name](**kwargs)
    with canonical_lock(board.canonical):
        publish_message(board.canonical, message, allow_no_remote=True)
    [published] = read_since(board.canonical, "test", after=0, include_unknown=True)

    assert published["from"] == "runtime"
    title, body = published["title"], published["body"]
    assert isinstance(title, str) and isinstance(body, str)
    assert title.strip() and "\n" not in title and len(title) <= rm.TITLE_LIMIT
    assert body.strip()
    for text in (title, body):
        assert OPAQUE.search(text) is None, text
        assert not [found for found in IDS if found in text], text
    assert all(isinstance(value, str) for value in published.get("about", {}).values())
