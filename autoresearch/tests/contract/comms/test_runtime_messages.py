"""Every message the runtime publishes says in plain words what happened (doctrine/
communication.md), with no ids or commits in its title or body (``comms.runtime_messages``).

The Softmax IDE bridge shows a runtime acknowledgement as the IDE task's outcome text, so these
titles and bodies reach humans verbatim. Ids go into ``refs`` and the additive ``about`` field.
"""

import ast
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
# Runtime ids (inbound, request, message, file-queue card, promotion), Kanban task ids, and
# commits, full or short (7-40 hex with a digit and a letter). Record ids (H-, E-, C-, ...) may
# appear inside an ar:// reference a rejection names; the runtime's own details use none.
OPAQUE = re.compile(
    r"\b[IQMKP]-[0-9A-HJKMNP-TV-Z]{26}\b|\bt_[0-9a-f]{4,}\b"
    r"|\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"
)
HYPOTHESIS_REF = {"uri": "ar://hypothesis/H-01M4EJ9RQMHVH6XK3N4T760MNF", "rev": SHA}
ACK_ABOUT = {"card": CARD, "request": REQUEST, "merge_commit": SHA, "outcome": "done"}

ACK_CASES = [  # outcome, summary, titles of the card's messages, backlog, asked for help
    (
        "done",
        "andre opens with two towers and rushes at minute 3.",
        ["Two-tower rush"],
        False,
        False,
    ),
    ("done", None, [], True, False),
    ("partial", None, ["A", "B", "C", f"About {REQUEST}"], False, False),
    ("dropped", None, [], False, False),
    ("failed", "The replay files are missing.", [], False, False),
    ("blocked", None, [f"Stuck at {SHA[:7]}"], False, True),
    ("blocked", None, [], False, False),
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
            "asked_for_help": asked,
            "refs": [HYPOTHESIS_REF],
            "about": ACK_ABOUT | {"outcome": outcome},
        }
        for outcome, summary, sent, backlog, asked in ACK_CASES
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
    "card_over_cap": [
        {
            "card_title": "Build and upload the baseline",
            "why": "it started run 5, and the cap is 4 runs per card",
            "allowance": "4 more runs and an estimated $4.00 of model spend",
            "task_id": CARD,
            "request_id": REQUEST,
            "stop": 1,
            "refs": [],
            "outcome": outcome,
        }
        for outcome in (rm.STOPPED, rm.TRIAGED, rm.HELD, rm.NOT_STOPPED)
    ],
    "card_models_invalid": [
        {"value": "planing=z-ai/glm-5.3", "problems": ["unknown card mode 'planing' (skipped)"]}
    ],
    "daily_budget_reached": [{"spent_usd": 25.31, "budget_usd": 25.0, "day": "2026-10-08"}],
    "signal_persists": [
        {
            "code": "stale_card",
            "entity": CARD,
            "detail": "the card “Scout andre” has been pending 4 days",
            "first_seen": SINCE,
            "iterations": 3,
        },
        {
            "code": "thrash",
            "entity": "E-01M4EJ9RQMHVH6XK3N4T760MNG",
            "detail": "an open experiment acts on lever SK.last_hit, which a refuted claim covers",
            "first_seen": SINCE,
            "iterations": 3,
        },
    ],
    "agent_paused": [{"reasons": "identity: wrong player", "paused_at": SINCE}],
    "agent_resumed": [{"count": 2}, {"count": 0, "champion": True}],
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
    "champion_conflict": [
        {"league_id": "league_x", "expected": exp, "shown": shown, "waited_out": waited}
        for exp, shown, waited in (
            ("ar-E-01M4EJ9RQMHVH6XK3N4T760MNG:v1", "hand-made:v2", False),
            ("ar-bootstrap-0123456789ab:v1", None, True),
            (None, "hand-made:v2", False),
        )
    ],
    "submission_rejected": [
        {
            "league_id": "league_x",
            "promotion_id": PROMOTION,
            "policy": "ar-E-01M4EJ9RQMHVH6XK3N4T760MNG:v1",
            "replaced": replaced,
            "detail": "membership disqualified (inactive)",
            "bootstrap": bootstrap,
        }
        for replaced, bootstrap in (("hand-made:v2", False), (None, True))
    ],
    "submission_pending": [
        {
            "league_id": "league_x",
            "promotion_id": PROMOTION,
            "policy": "ar-bootstrap-0123456789ab:v1",
            "detail": "membership qualifying (held)",
            "hours": 24,
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


def _names(tree: ast.AST) -> set[str]:
    """Every name a module binds or reads: names, attributes, imported names and aliases."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name)
    return found


def test_runtime_code_publishes_only_built_messages():
    """``publish_runtime`` takes free text; runtime code publishes ``RuntimeMessage``s only.
    No module but the publisher names it (so no alias or partial of it either), and inside the
    publisher only ``publish_message`` calls it."""
    users = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path.name != "publisher.py"
        and "publish_runtime" in _names(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert users == []
    publisher_tree = ast.parse((SRC / "comms" / "publisher.py").read_text(encoding="utf-8"))
    callers = {
        function.name
        for function in ast.walk(publisher_tree)
        if isinstance(function, ast.FunctionDef)
        and function.name != "publish_runtime"
        and "publish_runtime" in _names(function)
    }
    assert callers == {"publish_message"}


def test_a_signal_about_a_record_refers_to_it():
    [_stale, thrash] = FIXTURES["signal_persists"]
    message = rm.signal_persists(**thrash)
    assert message.refs == [
        {"uri": "ar://experiment/E-01M4EJ9RQMHVH6XK3N4T760MNG", "role": "subject"}
    ]


def test_plain_ids_names_what_an_id_points_to():
    text = f"Created ar://hypothesis/H-01M4EJ9RQMHVH6XK3N4T760MNF from {CARD} at {SHA[:12]}."
    assert rm.plain_ids(text) == "Created the hypothesis from the card at a commit."
    assert rm.plain_ids(f"Request {REQUEST} and E-01M4EJ9RQMHVH6XK3N4T760MNG") == (
        "Request the request and the experiment"
    )
    assert rm.plainest_summary([None, f"Made {FILE_CARD}.", "Plain words."]) == "Plain words."
    assert rm.plainest_summary(["", None]) is None
    claim = "ar://claim/C-01M4EJ9RQMHVH6XK3N4T760MNH"
    assert rm.plain_ids(f"See {claim}. Also {claim}, then ({claim}).") == (
        "See the claim. Also the claim, then (the claim)."
    )


def test_a_pre_upgrade_proposal_is_reraised_without_its_hash():
    legacy = OPEN_PROPOSAL | {"title": "Skill proposal: replay-diff (0123456789ab)"}
    assert rm.proposal_still_open(legacy, SHA).title == "Still open: Skill proposal: replay-diff"


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


@pytest.mark.parametrize(
    "name,kwargs",
    [(name, kwargs) for name, cases in FIXTURES.items() for kwargs in cases],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_runtime_message_carries_the_envelope_fields_of_its_kind(tmp_path, name, kwargs):
    """Whoever publishes a message gets the same priority and expects_response: the first
    publisher of a ``runtime_key`` wins, so they must not depend on the call site."""
    board = Board(tmp_path)
    message = _builders()[name](**kwargs)
    with canonical_lock(board.canonical):
        publish_message(board.canonical, message, allow_no_remote=True)
    [published] = read_since(board.canonical, "test", after=0, include_unknown=True)

    if message.kind == "help_request":
        assert published["priority"] == "P0"
        assert published["expects_response"] is True
    else:
        assert published.get("expects_response") is False
