"""Foreign cards: board tasks the runtime did not create (Overview C3, C7).

Admission creates every card from a committed create request (``cards/requests/<Q-id>.json``)
and writes the card spec into the card's body. Two ways to meet a card it did not create:

- **A forged handoff** (decided from the ledger, ``process_handoffs``): a handoff whose request
  has no committed create request (``admission.admitted_spec``). On 2026-10-09 on the box a
  worker of a ``kanban_create`` card wrote its own receipt into canonical so ``card finish``
  took its handoff. The board is never asked: a forged receipt can name any board epoch.
- **A foreign task on the board** (``report_foreign_cards``, a queue-keeper step): a task with
  no card body (``Queue.foreign_cards``), for example one a model created with
  ``kanban_create`` (now refused by the plugin) that just runs. A task named by a receipt whose
  request is committed is an admitted card whose body cannot be read: an error retried next
  run, never foreign. The spend caps also apply to foreign tasks (``spend.enforce_cards``).

Either way the card is recorded once (``record_foreign``): a committed marker
``ledger/foreign/<task-id>.json`` and one runtime ``help_request`` (``runtime_key``
``foreign-card:<task-id>``) that says what the card is and how a human archives it. The
runtime never merges a foreign card's work, in any board epoch: ``process_handoffs`` and
``rebuild`` check the marker and the ledger. A human who wants its work sends it as a new task.
"""

from pathlib import Path

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.queue.base import Queue
from autoresearch.runctx import BOARD, RunContext
from autoresearch.runtime import provenance
from autoresearch.runtime.admission import admitted_spec
from autoresearch.runtime.handoff import HANDOFFS_DIR
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso

FOREIGN_DIR = "foreign"


def foreign_marker_path(canonical: Path, task_id: str) -> Path:
    return ledger_of(canonical) / FOREIGN_DIR / f"{task_id}.json"


def is_foreign(canonical: Path, task_id: str) -> bool:
    """Whether ``task_id`` was recorded as foreign (committed marker)."""
    return committed_json(canonical, foreign_marker_path(canonical, task_id)) is not None


def record_foreign(
    canonical: Path,
    task_id: str,
    title: str | None,
    board: str,
    allow_no_remote: bool,
    forged_handoff: bool = False,
) -> bool:
    """Publish the help_request and commit the marker for a foreign card, once. Returns True if
    this call recorded it. ``forged_handoff``: found through a handoff its worker wrote (the
    message then asks for the operator). Takes the canonical lock: the caller must not hold
    it."""
    marker = foreign_marker_path(canonical, task_id)
    with canonical_lock(canonical):
        if committed_json(canonical, marker) is not None:
            return False
        message = runtime_messages.foreign_card(title, task_id, board, forged_handoff)
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote)
        write_json(marker, {"task_id": task_id, "title": title, "board": board, "at": now_iso()})
        commit(canonical, [marker], f"foreign card {task_id} reported")
    return True


def _admitted_tasks(canonical: Path) -> set[str]:
    """Task ids named by committed receipts whose request is committed (admitted cards)."""
    directory = ledger_of(canonical) / "requests"
    rel = directory.relative_to(canonical).as_posix()
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{rel}/")
    tasks: set[str] = set()
    for name in listing.splitlines():
        receipt = committed_json(canonical, canonical / name)
        if receipt and admitted_spec(canonical, receipt["request_id"]) is not None:
            tasks.update(task["task_id"] for task in receipt.get("tasks", []))
    return tasks


def _has_handoff(canonical: Path, task_id: str) -> bool:
    """Whether a handoff of ``task_id`` is committed (its worker forged one: no admitted card
    reaches here)."""
    return bool(gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{HANDOFFS_DIR}/{task_id}/"))


def report_foreign_cards(ctx: RunContext, queue: Queue, allow_no_remote: bool) -> list[str]:
    """Record each foreign task on the board once (module docstring); return the task ids
    recorded by this call. An admitted card whose body cannot be read, or a card whose
    recording fails, does not stop the others: they are raised together at the end, so the
    step is logged as failed and retried next run. Before the board exists (the rebuild step
    creates it) there is nothing to report. The caller must not hold the lock."""
    if not queue.board_exists():
        return []
    canonical = ctx.canonical
    admitted = None
    recorded, failures, journal = [], [], None
    for card in queue.foreign_cards():
        if is_foreign(canonical, card.task_id):
            continue
        try:
            admitted = _admitted_tasks(canonical) if admitted is None else admitted
            if card.task_id in admitted:
                failures.append(f"{card.task_id}: an admitted card's body cannot be read")
                continue
            title = card.spec.title or None
            forged = _has_handoff(canonical, card.task_id)
            if record_foreign(canonical, card.task_id, title, BOARD, allow_no_remote, forged):
                recorded.append(card.task_id)
        except Exception as error:  # noqa: BLE001 - per-card containment, raised below
            failures.append(f"{card.task_id}: {type(error).__name__}: {error}")
            journal = journal or provenance.journal_error(error)
    message = "foreign card check: " + "; ".join(failures)
    if journal is not None:  # typed, so queue-keeper fails closed
        raise provenance.JournalError(message) from journal
    if failures:
        raise RuntimeError(message)
    return recorded
