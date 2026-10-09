from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.docs import read_json
from autoresearch.runtime.pause import hold, is_held, is_paused, pause, release_hold, resume
from runtime.workspace import make_spec


def _create(queue, assignee):
    spec = make_spec()
    task_id = queue.create_unassigned(spec, f"card/{spec.request_id}", Path("/w"))
    queue.assign(task_id, assignee)
    return task_id


def _tracked(canonical, path):
    return bool(gitops.git(canonical.root, "ls-files", path))


def test_pause_unassigns_only_pending_and_resume_restores(canonical, queue):
    ledger = canonical.layout.ledger
    pending_a = _create(queue, "autoresearch")
    pending_b = _create(queue, "other-profile")
    unassigned = _create(queue, None)
    claimed = _create(queue, "worker-x")
    queue.claim_next("worker-x")

    paused = pause(queue, ledger, "identity failure")

    assert sorted(paused) == sorted([pending_a, pending_b])
    assert queue.get(pending_a).assignee is None and queue.get(pending_b).assignee is None
    assert queue.get(claimed).assignee == "worker-x"
    record = read_json(ledger / "pause.json")
    assert record["reason"] == "identity failure" and record["paused_at"]
    assert record["tasks"] == {pending_a: "autoresearch", pending_b: "other-profile"}
    assert _tracked(canonical, ".supervisor/ledger/pause.json")

    resumed = resume(queue, ledger)

    assert sorted(resumed) == sorted([pending_a, pending_b])
    assert queue.get(pending_a).assignee == "autoresearch"
    assert queue.get(pending_b).assignee == "other-profile"
    assert queue.get(unassigned).assignee is None
    assert not (ledger / "pause.json").exists()
    assert not _tracked(canonical, ".supervisor/ledger/pause.json")


def test_repeated_pause_keeps_recorded_assignees_and_merges_new(canonical, queue):
    ledger = canonical.layout.ledger
    first = _create(queue, "autoresearch")
    pause(queue, ledger, "identity failure")
    second = _create(queue, "autoresearch")

    assert pause(queue, ledger, "still failing") == [second]

    record = read_json(ledger / "pause.json")
    assert record["tasks"] == {first: "autoresearch", second: "autoresearch"}
    assert record["reason"] == "identity failure"
    assert sorted(resume(queue, ledger)) == sorted([first, second])
    assert queue.get(first).assignee == "autoresearch"


def test_resume_skips_tasks_no_longer_pending(canonical, queue):
    ledger = canonical.layout.ledger
    task = _create(queue, "autoresearch")
    pause(queue, ledger, "identity failure")
    queue.drop(task, "obsolete")

    assert resume(queue, ledger) == []
    assert queue.get(task).assignee is None


def test_resume_without_pause_is_a_noop(canonical, queue):
    assert resume(queue, canonical.layout.ledger) == []


def test_resume_skips_a_recorded_task_that_is_gone(canonical, queue, tmp_path):
    from autoresearch.queue.file_queue import FileQueue

    ledger = canonical.layout.ledger
    kept = _create(queue, "autoresearch")
    pause(queue, ledger, "identity failure")
    lost_board = FileQueue(tmp_path / "other-board")  # the recorded task is not on it
    lost_board.ensure_board()

    assert resume(lost_board, ledger) == []
    assert not (ledger / "pause.json").exists()
    assert queue.get(kept).assignee is None


# --- the coach hold (control pause and resume, D15) ---


def test_hold_unassigns_pending_cards_and_release_restores_them(canonical, queue):
    ledger = canonical.layout.ledger
    pending = _create(queue, "autoresearch")
    claimed = _create(queue, "worker-x")
    queue.claim_next("worker-x")

    assert hold(queue, ledger, "coach:ply_x") == [pending]

    record = read_json(ledger / "coach-hold.json")
    assert record["by"] == "coach:ply_x" and record["held_at"]
    assert record["tasks"] == {pending: "autoresearch"}
    assert _tracked(canonical, ".supervisor/ledger/coach-hold.json")
    assert is_held(ledger) and not is_paused(ledger)
    assert queue.get(pending).assignee is None and queue.get(claimed).assignee == "worker-x"
    assert hold(queue, ledger, "coach:ply_y") == []  # idempotent: the first hold is kept
    assert read_json(ledger / "coach-hold.json")["by"] == "coach:ply_x"

    assert release_hold(queue, ledger) == [pending]
    assert queue.get(pending).assignee == "autoresearch"
    assert not is_held(ledger) and not _tracked(canonical, ".supervisor/ledger/coach-hold.json")
    assert release_hold(queue, ledger) == []  # no hold: a no-op


def test_releasing_a_hold_under_a_pause_leaves_its_cards_to_the_pause(canonical, queue):
    ledger = canonical.layout.ledger
    task = _create(queue, "autoresearch")
    hold(queue, ledger, "coach:ply_x")
    assert pause(queue, ledger, "identity failure") == []  # the hold already unassigned it

    assert release_hold(queue, ledger) == []

    assert not is_held(ledger) and is_paused(ledger)
    assert queue.get(task).assignee is None  # still paused
    record = read_json(ledger / "pause.json")
    assert record["tasks"] == {task: "autoresearch"} and record["reason"] == "identity failure"
    assert not _tracked(canonical, ".supervisor/ledger/coach-hold.json")
    assert resume(queue, ledger) == [task]
    assert queue.get(task).assignee == "autoresearch"
