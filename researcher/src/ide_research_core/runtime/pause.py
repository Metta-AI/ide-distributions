"""Pause and resume dispatch on our board (Overview C7), and the coach hold (D15).

Pause unassigns every pending card that has an assignee; unassigned cards are never
dispatched. ``ledger/pause.json`` ``{reason, paused_at, tasks: {task_id: prior_assignee}}``
records the prior assignees, and is written (and committed) before any card is unassigned, so
a crash never loses an assignee. A commit that fails (a broken provenance journal fails every
canonical commit) still unassigns: the written file is the pause. While ``pause.json`` exists,
admission is stopped (see admission.py). queue-keeper lifts it on its own once the cause
clears (preflight passes, and so on).

The coach hold is the same mechanism with its own record, ``ledger/coach-hold.json``
``{held_at, by, tasks}`` (``by``: the author of the ``control`` that paused), set and
cleared only by the coach (``control`` pause and resume, ``runtime.routing``). While it
exists, admission and planning are stopped too, and queue-keeper never lifts ``pause.json``
(which would reassign cards under the hold) and never clears the hold. Releasing the hold while
``pause.json`` exists moves its recorded cards into ``pause.json``, so they wait for that
pause to lift. Running (claimed) cards keep running under both, under their spend caps.
Unlike a pause, a hold whose commit fails unassigns nothing: the ``JournalError`` reaches
routing and queue-keeper's fail-closed pause, and the ``control`` is routed again next run.
"""

from pathlib import Path

from ide_research_core.io import gitops
from ide_research_core.io.docs import read_json, write_json
from ide_research_core.queue.base import Queue
from ide_research_core.runtime.ledger import (
    canonical_lock,
    canonical_of,
    commit,
    committed_json,
    now_iso,
)

PAUSE_FILE = "pause.json"
HOLD_FILE = "coach-hold.json"


def pause_path(ledger: Path) -> Path:
    return Path(ledger) / PAUSE_FILE


def is_paused(ledger: Path) -> bool:
    return pause_path(ledger).exists()


def hold_path(ledger: Path) -> Path:
    return Path(ledger) / HOLD_FILE


def is_held(ledger: Path) -> bool:
    """True while the coach holds research (``control`` pause)."""
    return hold_path(ledger).exists()


def _assigned_pending(queue: Queue) -> dict[str, str]:
    return {card.task_id: card.assignee for card in queue.list("pending") if card.assignee}


def _record_assignees(path: Path, fresh: dict, assigned: dict[str, str]) -> dict:
    """Write ``path``'s record (``fresh`` when absent) with the ``assigned`` cards it does not
    list yet; return it. The caller holds the canonical lock and commits."""
    record = read_json(path) if path.exists() else fresh
    record["tasks"].update(
        {task: who for task, who in assigned.items() if task not in record["tasks"]}
    )
    write_json(path, record)
    return record


def _reassign(queue: Queue, tasks: dict[str, str], ledger: Path) -> list[str]:
    """Reassign exactly the recorded cards that are still pending and unassigned. A recorded
    card that is no longer on the board (a lost board) is skipped."""
    on_board = {card.task_id: card for card in queue.list()}
    reassigned = []
    for task_id, assignee in tasks.items():
        card = on_board.get(task_id)
        if card is None:
            continue
        receipt = committed_json(
            canonical_of(ledger), ledger / "requests" / f"{card.spec.request_id}.json"
        )
        if receipt is not None and receipt.get("cancelled_by"):
            continue
        if card.state == "pending" and card.assignee is None:
            queue.assign(task_id, assignee)
            reassigned.append(task_id)
    return reassigned


def pause(queue: Queue, ledger: Path, reason: str) -> list[str]:
    """Unassign pending cards and record their assignees; return the task ids unassigned now.

    Idempotent: a repeated pause keeps the original reason, time and recorded assignees, and
    only adds cards that were assigned since.
    """
    canonical = canonical_of(ledger)
    path = pause_path(ledger)
    assigned = _assigned_pending(queue)
    try:
        with canonical_lock(canonical):
            fresh = {"reason": reason, "paused_at": now_iso(), "tasks": {}}
            record = _record_assignees(path, fresh, assigned)
            commit(canonical, [path], f"pause: {record['reason']}")
    finally:
        # Stopping dispatch must not depend on the commit: a broken provenance journal fails
        # every canonical commit, and that is exactly when the pause matters. The written
        # pause file is enough for ``is_paused`` and ``resume``.
        for task_id in assigned:
            queue.assign(task_id, None)
    return list(assigned)


def resume(queue: Queue, ledger: Path) -> list[str]:
    """Reassign the recorded cards (``_reassign``) and clear the pause; return the task ids
    reassigned. The caller makes sure the coach does not hold research (module docstring)."""
    canonical = canonical_of(ledger)
    path = pause_path(ledger)
    if not path.exists():
        return []
    reassigned = _reassign(queue, read_json(path)["tasks"], ledger)
    with canonical_lock(canonical):
        committed = gitops.show(canonical, "HEAD", path.relative_to(canonical).as_posix())
        path.unlink()
        if committed is not None:  # a pause whose commit failed (``pause``) was never committed
            commit(canonical, [path], "resume")
    return reassigned


def hold(queue: Queue, ledger: Path, by: str) -> list[str]:
    """Hold research for the coach: like ``pause``, with ``coach-hold.json`` as the record
    (``by``: the control's author). Idempotent: a repeated hold keeps the first ``held_at``
    and ``by``. Unlike ``pause``, the caller holds the canonical lock (routing applies it).
    Return the task ids unassigned now."""
    canonical = canonical_of(ledger)
    path = hold_path(ledger)
    assigned = _assigned_pending(queue)
    record = _record_assignees(path, {"held_at": now_iso(), "by": by, "tasks": {}}, assigned)
    commit(canonical, [path], f"coach hold: {record['by']}")
    for task_id in assigned:
        queue.assign(task_id, None)
    return list(assigned)


def release_hold(queue: Queue, ledger: Path) -> list[str]:
    """Clear the coach hold; return the task ids reassigned. While ``pause.json`` exists the
    hold's recorded cards move into it instead (they wait for that pause to lift), in the same
    commit that removes the hold. The caller holds the canonical lock; no hold is a no-op."""
    canonical = canonical_of(ledger)
    path = hold_path(ledger)
    if not path.exists():
        return []
    tasks = read_json(path)["tasks"]
    paused = pause_path(ledger)
    if paused.exists():
        _record_assignees(paused, read_json(paused), tasks)
        path.unlink()
        commit(canonical, [paused, path], "coach resume: cards wait for the pause")
        return []
    reassigned = _reassign(queue, tasks, ledger)
    path.unlink()
    commit(canonical, [path], "coach resume")
    return reassigned
