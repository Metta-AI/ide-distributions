"""Comms contract suite (Runtime 9.3): a fake Preston drives ``autoresearch comms`` over
subprocesses against a canonical workspace that real workers publish into."""

import functools
import json

import pytest
from fake_preston import CommsError, run_comms

from autoresearch.comms.publisher import publish_hook, publish_runtime
from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.runtime.ledger import canonical_lock

# The canonical repos here have no remote.
local_hook = functools.partial(publish_hook, allow_no_remote=True)

HYPOTHESIS = {
    "statement": "Raising credits unblocks the experiment queue.",
    "prediction": "Two experiments run within an hour.",
    "topics": [],
    "lever": "XP.credits",
    "state": "proposed",
}


def _worker_env(base_env: dict, board, worker) -> dict:
    return base_env | {
        "AUTORESEARCH_WORKSPACE": str(board.canonical),
        "HERMES_KANBAN_WORKSPACE": str(worker.worktree),
        "HERMES_KANBAN_TASK": worker.task_id,
        "HERMES_KANBAN_RUN_ID": worker.run_id,
    }


def _stage(base_env, board, worker, kind: str, title: str, *refs: str, **fields) -> str:
    args = ["stage", kind, "--title", title, "--body", f"{title}. Written for Preston."]
    for ref in refs:
        args += ["--ref", ref]
    for name, value in fields.items():
        args += [f"--{name.replace('_', '-')}", value]
    return run_comms(_worker_env(base_env, board, worker), *args)["id"]


def _question_card(board, base_env) -> tuple[str, str]:
    """A card that creates a hypothesis and asks a question about it; returns (M-id, H-id)."""
    worker = board.admit()
    worker.start()
    hypothesis = worker.store.create("hypothesis", dict(HYPOTHESIS), "Credits.\n")
    worker.write_journal()
    question = _stage(
        base_env,
        board,
        worker,
        "question",
        "Raise XP credits?",
        f"ar://hypothesis/{hypothesis}",
        json.dumps({"uri": "ar://file/AGENTS.md", "rev": "@result", "role": "context"}),
        thread="credits",
    )
    worker.finish()
    board.process(publish=local_hook)
    return question, hypothesis


def _ack_card(board, base_env, inbound_id: str) -> str:
    """A card responsible for ``inbound_id`` that acknowledges it with a note."""
    worker = board.admit()
    card_file = board.canonical / ".supervisor" / "runtime" / "cards" / f"{worker.task_id}.json"
    write_json(card_file, read_json(card_file) | {"responsible_inbound": [inbound_id]})
    worker.start()
    worker.write_journal()
    note = _stage(
        base_env, board, worker, "note", "Credits raised; queue resumes", in_reply_to=inbound_id
    )
    record = worker.finish()
    assert record["responsible_inbound"] == [inbound_id]
    [outcome] = board.process(publish=local_hook)
    assert outcome.state == "published"
    return note


def test_round_trip_read_resolve_reply_seen_and_acknowledgement(board, base_env, preston):
    question, hypothesis = _question_card(board, base_env)

    [message] = preston.read()
    assert message["id"] == question and message["kind"] == "question" and message["seq"] == 1
    resolved = preston.resolve_all(message)
    assert [item["path"] for item in resolved] == [
        board.store.path_of(hypothesis).relative_to(board.canonical).as_posix(),
        "AGENTS.md",
    ]
    assert all(item["rev"] == message["published_rev"] for item in resolved)
    assert "Raising credits" in resolved[0]["content"]

    reply = preston.reply(question, "Yes. Credits are raised to 200.")
    preston.seen(message["id"])
    status = preston.status(question)
    assert status["published"] and status["seq"] == 1 and status["seen_by"] == ["preston"]
    inbox = json.loads(gitops.show(board.canonical, "HEAD", f"comms/inbox/{reply}.json"))
    assert inbox["kind"] == "reply" and inbox["in_reply_to"] == question
    assert inbox["author"] == "preston"

    note = _ack_card(board, base_env, reply)
    [ack] = preston.read()
    assert ack["id"] == note and ack["kind"] == "note" and ack["in_reply_to"] == reply
    assert ack["seq"] == 2
    assert preston.read() == []


def test_two_consumers_keep_independent_cursors(board, base_env, preston, ide):
    question, _ = _question_card(board, base_env)
    assert [m["id"] for m in preston.read()] == [question]
    second, _ = _question_card(board, base_env)
    assert [m["id"] for m in ide.read()] == [question, second]
    assert [m["id"] for m in preston.read()] == [second]
    assert preston.read() == [] and ide.read() == []
    assert [m["id"] for m in preston.comms("list", "--after", "0")["messages"]] == [
        question,
        second,
    ]
    assert preston.get(1)["id"] == question and preston.get(second)["seq"] == 2


def test_unknown_additive_fields_and_kinds_are_ignored_by_the_reader(board, preston):
    with canonical_lock(board.canonical):
        seq = publish_runtime(
            board.canonical,
            "note",
            "Queue paused",
            "Disk is low.",
            [],
            allow_no_remote=True,
            future_field={"x": 1},
        ).seq
    # A newer writer publishes a kind this reader does not know.
    future_id = new_id("message")
    future = {
        "schema": "ar-comms/0",
        "id": future_id,
        "kind": "poll",
        "title": "Pick one",
        "body": "",
        "refs": [],
        "from": "runtime",
        "created": "2026-10-07T00:00:00+00:00",
        "iteration": None,
        "priority": "P2",
        "expects_response": True,
        "supersedes": None,
        "expires": None,
        "seq": seq + 1,
        "published_rev": gitops.head(board.canonical),
        "options": ["a", "b"],
    }
    outbox = board.canonical / "comms" / "outbox" / f"{seq + 1:08d}-{future_id}.json"
    write_json(outbox, future)
    receipt = board.canonical / ".supervisor" / "ledger" / "published" / f"{future_id}.json"
    write_json(
        receipt,
        {"id": future_id, "seq": seq + 1, "file": outbox.relative_to(board.canonical).as_posix()},
    )
    gitops.commit_all(board.canonical, "a future writer")

    data = preston.comms("list", "--consumer", "preston", "--advance")
    assert [m["seq"] for m in data["messages"]] == [seq]
    assert data["messages"][0]["future_field"] == {"x": 1}
    assert data["last_seq"] == seq + 1
    assert preston.read() == []  # the cursor moved past the unknown kind
    assert preston.get(seq + 1)["kind"] == "poll"


@pytest.mark.parametrize(
    ("kind", "payload", "needle"),
    [
        ("steer", {"text": "Go faster", "urgency": "yesterday"}, "urgency"),
        (
            "steer",
            {"text": "Look at towers", "urgency": "next", "proposed_topic": {"title": "Towers"}},
            "proposed_topic.objective",
        ),
        ("reply", {"in_reply_to": "M-1", "text": "yes"}, "in_reply_to"),
        ("reply", {"in_reply_to": "M-01K6Z3V8QX0000000000000000"}, "choice or text"),
        ("seen", {"consumer": "preston", "seq": 1}, "ids"),
        ("seen", {"ids": ["M-01K6Z3V8QX0000000000000000"]}, "not a published message"),
        ("task", {"title": "T", "mode": "scouting", "why": "W", "refs": []}, "priority"),
        (
            "task",
            {"title": "T", "mode": "dancing", "why": "W", "refs": [], "priority": "P1"},
            "mode",
        ),
    ],
)
def test_inbound_validation(board, preston, kind, payload, needle):
    with pytest.raises(CommsError) as caught:
        preston.send(kind, **payload)
    assert caught.value.code == 1
    assert any(needle in problem for problem in caught.value.payload["error"]["problems"])


def test_inbound_needs_a_known_kind_and_an_author(board, preston):
    with pytest.raises(CommsError):
        preston.send("dance", text="x")
    with pytest.raises(CommsError) as caught:  # argparse: --author is required
        preston.comms("inbound", "steer", "--json", '{"text": "x", "urgency": "now"}')
    assert caught.value.code == 2


def test_valid_steer_with_proposed_topic_is_recorded(board, preston):
    topic = {
        "title": "Towers",
        "objective": "Stop dying to towers.",
        "metrics": [],
        "priority": "P2",
    }
    inbound = preston.send("steer", text="Look at towers", urgency="now", proposed_topic=topic)
    record = json.loads(gitops.show(board.canonical, "HEAD", f"comms/inbox/{inbound}.json"))
    assert record["proposed_topic"] == topic and record["author"] == "preston"


@pytest.mark.xfail(strict=True, reason="Task 3.7: inbound task routing and acknowledgement")
def test_inbound_task_is_acknowledged_when_its_card_publishes(board, ide):
    task = ide.task("Profile opponent andre", "scouting", "Andre leads the league.", [], "P1")
    record = json.loads(gitops.show(board.canonical, "HEAD", f"comms/inbox/{task}.json"))
    assert record["kind"] == "task" and record["author"] == "ide"
    # Task 3.7 routes the task to its own card; here the card is admitted by hand.
    worker = board.admit(mode="scouting")
    worker.start()
    worker.write_journal()
    worker.finish()
    board.process(publish=local_hook)
    assert any(
        message["kind"] == "note" and message.get("in_reply_to") == task for message in ide.read()
    )
