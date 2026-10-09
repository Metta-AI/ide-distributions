import multiprocessing

import pytest

from autoresearch.queue.card import CardSpec
from autoresearch.queue.file_queue import FileQueue


def make_spec(n: int, priority="P2", order=0) -> CardSpec:
    return CardSpec(
        f"Q-{n:026d}", f"card {n}", "investigation", "why", [], "tester", priority, order
    )


@pytest.fixture
def queue(tmp_path):
    return FileQueue(tmp_path / "fq")


def add(queue, tmp_path, n, assignee="w", **kwargs):
    task_id = queue.create_unassigned(make_spec(n, **kwargs), f"card/{n}", tmp_path / str(n))
    if assignee:
        queue.assign(task_id, assignee)
    return task_id


def test_create_is_idempotent(queue, tmp_path):
    first = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    again = queue.create_unassigned(make_spec(1), "card/1", tmp_path)
    assert first == again and first.startswith("K-")
    assert len(queue.list()) == 1
    assert queue.get(first).state == "pending" and queue.get(first).assignee is None


def test_unassigned_never_claimed(queue, tmp_path):
    add(queue, tmp_path, 1, assignee=None)
    assert queue.claim_next("w") is None


def test_only_matching_assignee_claims(queue, tmp_path):
    add(queue, tmp_path, 1, assignee="other")
    assert queue.claim_next("w") is None


def test_unassign_removes_from_claimable(queue, tmp_path):
    task_id = add(queue, tmp_path, 1)
    queue.assign(task_id, None)
    assert queue.claim_next("w") is None


def test_claim_order_priority_then_oldest(queue, tmp_path):
    low = add(queue, tmp_path, 1, priority="P3")
    first_p1 = add(queue, tmp_path, 2, priority="P1")
    second_p1 = add(queue, tmp_path, 3, priority="P1")
    boosted = add(queue, tmp_path, 4, priority="P1", order=2)
    claimed = [queue.claim_next("w").task_id for _ in range(4)]
    assert claimed == [boosted, first_p1, second_p1, low]
    assert queue.claim_next("w") is None


def test_claim_creates_run_and_finish_completes(queue, tmp_path):
    add(queue, tmp_path, 1)
    card = queue.claim_next("w")
    assert card.state == "claimed"
    run = card.runs[0]
    assert run.run_id.startswith("r-") and not run.terminal
    queue.finish(card.task_id, run.run_id, "published")
    done = queue.get(card.task_id)
    assert done.state == "completed"
    assert done.runs[0].terminal and done.runs[0].outcome == "published"
    queue.finish(card.task_id, run.run_id, "published")  # idempotent
    with pytest.raises(ValueError):
        queue.finish(card.task_id, run.run_id, "failed")
    with pytest.raises(ValueError):
        queue.finish(card.task_id, "r-nope", "published")


def test_drop_and_reprioritize_only_pending(queue, tmp_path):
    pending = add(queue, tmp_path, 1)
    queue.reprioritize(pending, "P0", 4)
    assert queue.get(pending).spec.priority == "P0" and queue.get(pending).spec.order == 4
    queue.drop(pending, "obsolete")
    dropped = queue.get(pending)
    assert dropped.state == "completed" and dropped.runs[-1].outcome == "dropped"

    add(queue, tmp_path, 2)
    claimed = queue.claim_next("w")
    with pytest.raises(ValueError):
        queue.drop(claimed.task_id, "x")
    with pytest.raises(ValueError):
        queue.reprioritize(claimed.task_id, "P0")
    with pytest.raises(ValueError):
        queue.drop(pending, "again")


def test_requeue_claimed_returns_to_pending(queue, tmp_path):
    add(queue, tmp_path, 1)
    card = queue.claim_next("w")
    queue.requeue(card.task_id, "worker died")
    requeued = queue.get(card.task_id)
    assert requeued.state == "pending" and requeued.runs[0].terminal
    again = queue.claim_next("w")
    assert again.task_id == card.task_id and len(again.runs) == 2
    queue.finish(again.task_id, again.runs[1].run_id, "ok")
    with pytest.raises(ValueError):
        queue.requeue(again.task_id, "too late")


def test_list_filters_by_state(queue, tmp_path):
    add(queue, tmp_path, 1)
    add(queue, tmp_path, 2)
    queue.claim_next("w")
    assert len(queue.list("pending")) == 1
    assert len(queue.list("claimed")) == 1


def _claim_all(args):
    directory, worker = args
    queue = FileQueue(directory)
    claimed = []
    while (card := queue.claim_next(worker)) is not None:
        claimed.append(card.task_id)
    return claimed


def test_concurrent_claims_never_double_claim(queue, tmp_path):
    total = 20
    for n in range(total):
        add(queue, tmp_path, n)
    with multiprocessing.get_context("spawn").Pool(4) as pool:
        results = pool.map(_claim_all, [(queue.directory, "w")] * 4)
    claimed = [task_id for chunk in results for task_id in chunk]
    assert len(claimed) == total and len(set(claimed)) == total
    assert all(len(card.runs) == 1 for card in queue.list())


def test_board_exists_and_ensure_board(tmp_path):
    queue = FileQueue(tmp_path / "board")
    assert queue.board_exists() is False  # C12: a missing directory is a missing board
    queue.ensure_board()
    assert queue.board_exists() is True and queue.list() == []


def test_current_run_is_the_claimed_run(queue, tmp_path):
    """FileQueue has no heartbeats; the current run is the claimed one until it ends."""
    task_id = add(queue, tmp_path, 1)
    assert queue.get(task_id).current_run_id is None
    run_id = queue.claim_next("w").runs[-1].run_id
    card = queue.get(task_id)
    assert (card.current_run_id, card.last_heartbeat_at) == (run_id, None)
    queue.finish(task_id, run_id, "done")
    assert queue.get(task_id).current_run_id is None
