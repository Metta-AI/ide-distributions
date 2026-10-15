"""Behaviour every Queue backend must share: FileQueue and KanbanQueue (stateful fake hermes)."""

import stat

import pytest

from ide_research_core.queue.card import CardSpec
from ide_research_core.queue.file_queue import FileQueue
from ide_research_core.queue.kanban_queue import KanbanQueue

# A small in-memory-on-disk imitation of the Hermes Kanban commands KanbanQueue uses.
STATEFUL_FAKE_HERMES = """#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_HERMES_STATE"]
state = json.load(open(path)) if os.path.exists(path) else {"tasks": [], "next": 1}
args = sys.argv[1:]
if args[:2] == ["kanban", "boards"]:
    sys.exit(0)
args = args[3:]  # drop: kanban --board <b>
command, rest = args[0], args[1:]

def flag(name):
    return rest[rest.index(name) + 1] if name in rest else None

def find(task_id):
    return next(t for t in state["tasks"] if t["task"]["id"] == task_id)

def save():
    json.dump(state, open(path, "w"))

if command == "create":
    key = flag("--idempotency-key")
    existing = [t for t in state["tasks"] if t["key"] == key]
    if existing:
        print(json.dumps(existing[0]["task"]))
    else:
        task = {"id": f"t_{state['next']}", "title": rest[0], "assignee": None,
                "status": "ready", "body": open(flag("--body-file")).read(),
                "priority": int(flag("--priority"))}
        state["next"] += 1
        state["tasks"].append({"task": task, "key": key, "runs": []})
        save()
        print(json.dumps(task))
elif command == "list":
    print(json.dumps([t["task"] for t in state["tasks"]]))
elif command == "show":
    entry = find(rest[0])
    print(json.dumps({"task": entry["task"], "runs": entry["runs"]}))
elif command == "assign":
    find(rest[0])["task"]["assignee"] = None if rest[1] == "none" else rest[1]
    save()
elif command == "edit":
    entry = find(rest[0])
    entry["task"]["priority"] = int(flag("--priority"))
    entry["task"]["body"] = flag("--body")
    save()
elif command == "complete":
    entry = find(rest[0])
    entry["task"]["status"] = "done"
    entry["runs"].append({"id": len(entry["runs"]) + 1, "outcome": "completed",
                          "ended_at": 1, "metadata": json.loads(flag("--metadata"))})
    save()
elif command == "block":
    entry = find(rest[0])
    if entry["task"]["status"] not in ("ready", "running"):
        sys.exit("cannot block")
    for run in entry["runs"]:
        if run["ended_at"] is None:
            run.update(ended_at=1, outcome="blocked", summary=rest[1])
            break
    else:
        entry["runs"].append({"id": len(entry["runs"]) + 1, "outcome": "blocked",
                              "ended_at": 1, "metadata": None, "summary": rest[1]})
    entry["task"]["status"] = "blocked"
    save()
else:
    sys.exit("unsupported: " + command)
"""


def make_spec(n: int) -> CardSpec:
    return CardSpec(f"Q-{n:026d}", f"card {n}", "investigation", "why", [], "tester", "P2", 0)


@pytest.fixture(params=["file", "kanban"])
def queue(request, tmp_path, monkeypatch):
    if request.param == "file":
        return FileQueue(tmp_path / "fq")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "hermes"
    script.write_text(STATEFUL_FAKE_HERMES)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_HERMES_STATE", str(tmp_path / "state.json"))
    return KanbanQueue("b", "p")


def test_create_is_idempotent_and_unassigned(queue, tmp_path):
    first = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    assert queue.create_unassigned(make_spec(1), "card/1", tmp_path) == first
    assert len(queue.list()) == 1
    card = queue.get(first)
    assert card.state == "pending" and card.assignee is None and card.spec == make_spec(1)


def test_idempotency_key_override_creates_a_second_card(queue, tmp_path):
    first = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    again = queue.create_unassigned(make_spec(1), "card/1", tmp_path, idempotency_key="req:1:e2")
    assert again != first
    assert queue.create_unassigned(make_spec(1), "card/1", tmp_path, "req:1:e2") == again
    assert len(queue.list()) == 2


def test_assign_and_unassign(queue, tmp_path):
    task_id = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    queue.assign(task_id, "w")
    assert queue.get(task_id).assignee == "w"
    queue.assign(task_id, None)
    assert queue.get(task_id).assignee is None


def test_list_filters_by_state_and_returns_runs(queue, tmp_path):
    kept = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    dropped = queue.create_unassigned(make_spec(2), "card/2", tmp_path)
    queue.drop(dropped, "obsolete")
    assert [c.task_id for c in queue.list("pending")] == [kept]
    (completed,) = queue.list("completed")
    assert completed.task_id == dropped
    assert [(r.terminal, r.outcome) for r in completed.runs] == [(True, "dropped")]
    assert queue.get(dropped).runs == completed.runs


def test_reprioritize_changes_spec(queue, tmp_path):
    task_id = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    queue.reprioritize(task_id, "P0", 4)
    spec = queue.get(task_id).spec
    assert (spec.priority, spec.order) == ("P0", 4)
    with pytest.raises(ValueError):
        queue.reprioritize(task_id, "P0", 10)


def test_drop_and_reprioritize_only_pending(queue, tmp_path):
    task_id = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    queue.drop(task_id, "obsolete")
    with pytest.raises(ValueError):
        queue.drop(task_id, "again")
    with pytest.raises(ValueError):
        queue.reprioritize(task_id, "P1")


def test_requeue_pending_is_rejected(queue, tmp_path):
    task_id = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    with pytest.raises(ValueError):
        queue.requeue(task_id, "x")


def test_stop_blocks_a_pending_card_with_the_reason(queue, tmp_path):
    task_id = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    queue.stop(task_id, "spend-cap: 4 runs")
    card = queue.get(task_id)
    assert card.state == "blocked" and card.block_reason == "spend-cap: 4 runs"
    assert card.block_run_id == card.runs[-1].run_id and card.runs[-1].outcome == "blocked"
