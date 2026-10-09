"""Foreign cards: board tasks the runtime did not create (Overview C3, C7).

Admission writes the card spec into the body of every card it creates. A task without one
(``ForeignCardError`` from ``KanbanQueue.get``) has no request behind it: something put it on
the board directly, such as a model's ``kanban_create`` (now refused by the plugin). Its work
skipped admission, so the runtime never merges it, even when its worker forged a receipt and
left a handoff (2026-10-09 on the box: a worker wrote its own receipt into canonical so
``card finish`` would take its handoff).

``KanbanQueue.list`` already skips such tasks. Readers that look one up by id (handoff
processing) call ``record_foreign`` instead of failing: one committed marker
``ledger/foreign/<task-id>.json`` and one runtime ``help_request`` (``runtime_key``
``foreign-card:<task-id>``) that says what the card is and how a human archives it. The marker
is what later runs check (``is_foreign``) before touching the board, so a marked card is
skipped for good, in every board epoch. A human who wants its work sends it as a new task.
"""

from pathlib import Path

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io.docs import write_json
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso

FOREIGN_DIR = "foreign"


def foreign_marker_path(canonical: Path, task_id: str) -> Path:
    return ledger_of(canonical) / FOREIGN_DIR / f"{task_id}.json"


def is_foreign(canonical: Path, task_id: str) -> bool:
    """Whether ``task_id`` was recorded as foreign (committed marker)."""
    return committed_json(canonical, foreign_marker_path(canonical, task_id)) is not None


def record_foreign(
    canonical: Path, task_id: str, title: str | None, board: str, allow_no_remote: bool
) -> bool:
    """Publish the help_request and commit the marker for a foreign card, once. Returns True if
    this call recorded it. Takes the canonical lock: the caller must not hold it."""
    marker = foreign_marker_path(canonical, task_id)
    with canonical_lock(canonical):
        if committed_json(canonical, marker) is not None:
            return False
        message = runtime_messages.foreign_card(title, task_id, board)
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote)
        write_json(marker, {"task_id": task_id, "title": title, "board": board, "at": now_iso()})
        commit(canonical, [marker], f"foreign card {task_id} reported")
    return True
