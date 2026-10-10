"""Pause and resume dispatch on our board (Overview C7).

Pause unassigns every pending card that has an assignee; unassigned cards are never
dispatched. ``ledger/pause.json`` ``{reason, paused_at, tasks: {task_id: prior_assignee}}``
records the prior assignees, and is written (and committed) before any card is unassigned, so
a crash never loses an assignee. A commit that fails (a broken provenance journal fails every
canonical commit) still unassigns: the written file is the pause. While ``pause.json`` exists,
admission is stopped (see admission.py).
"""

from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.base import Queue
from autoresearch.runtime.ledger import canonical_lock, canonical_of, commit, now_iso

PAUSE_FILE = "pause.json"


def pause_path(ledger: Path) -> Path:
    return Path(ledger) / PAUSE_FILE


def is_paused(ledger: Path) -> bool:
    return pause_path(ledger).exists()


def pause(queue: Queue, ledger: Path, reason: str) -> list[str]:
    """Unassign pending cards and record their assignees; return the task ids unassigned now.

    Idempotent: a repeated pause keeps the original reason, time and recorded assignees, and
    only adds cards that were assigned since.
    """
    canonical = canonical_of(ledger)
    path = pause_path(ledger)
    assigned = {card.task_id: card.assignee for card in queue.list("pending") if card.assignee}
    try:
        with canonical_lock(canonical):
            record = (
                read_json(path)
                if path.exists()
                else {"reason": reason, "paused_at": now_iso(), "tasks": {}}
            )
            new_tasks = {t: who for t, who in assigned.items() if t not in record["tasks"]}
            record["tasks"].update(new_tasks)
            write_json(path, record)
            commit(canonical, [path], f"pause: {record['reason']}")
    finally:
        # Stopping dispatch must not depend on the commit: a broken provenance journal fails
        # every canonical commit, and that is exactly when the pause matters. The written
        # pause file is enough for ``is_paused`` and ``resume``.
        for task_id in assigned:
            queue.assign(task_id, None)
    return list(assigned)


def resume(queue: Queue, ledger: Path) -> list[str]:
    """Reassign exactly the recorded cards that are still pending and unassigned; clear the
    pause. A recorded card that is no longer on the board (a lost board) is skipped. Return
    the task ids reassigned."""
    canonical = canonical_of(ledger)
    path = pause_path(ledger)
    if not path.exists():
        return []
    on_board = {card.task_id: card for card in queue.list()}
    reassigned = []
    for task_id, assignee in read_json(path)["tasks"].items():
        card = on_board.get(task_id)
        if card is None:
            continue
        if card.state == "pending" and card.assignee is None:
            queue.assign(task_id, assignee)
            reassigned.append(task_id)
    with canonical_lock(canonical):
        committed = gitops.show(canonical, "HEAD", path.relative_to(canonical).as_posix())
        path.unlink()
        if committed is not None:  # a pause whose commit failed (``pause``) was never committed
            commit(canonical, [path], "resume")
    return reassigned
