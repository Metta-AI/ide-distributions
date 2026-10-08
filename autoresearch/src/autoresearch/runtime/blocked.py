"""Blocked cards: Hermes ``blocked`` (circuit breaker, bad credentials) is an operational
failure, never a research state (Runtime §5). queue-keeper and health both call
``handle_blocked``; it is idempotent.

Per blocked card, tracked by the committed marker
``.supervisor/ledger/health/blocked/<task-id>.json``:

1. First block: requeue the card once (``queue.requeue``, Kanban ``unblock``), then commit the
   marker ``{state: requeued}``.
2. Blocked again after that: one runtime repair request (marker ``repair-blocked:<task-id>``,
   the card's mode and refs, P1) and one runtime ``help_request``, then the marker becomes
   ``{state: repaired, repair_request}``. A card a promotion covers gets no repair request:
   the promotion-recovery card is its only follow-up (A11); the help_request still goes out.
3. Later: nothing more. The blocked card stays blocked for a human to inspect.

The queue change comes before the commit, so a crash between them at worst requeues a card
a second time; it never skips straight to a repair.
"""

from dataclasses import dataclass
from pathlib import Path

from autoresearch.comms.publisher import OUTBOX_DIR, publish_runtime
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Card, CardSpec
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.markers import repair_blocked_marker
from autoresearch.runtime.planning import RUNTIME, commit_runtime_request, marked_request

REQUEUED = "requeued"
REPAIRED = "repaired"
REPAIR_PRIORITY = "P1"


@dataclass
class BlockedAction:
    task_id: str
    action: str  # requeued | repaired
    repair_request: str | None = None


def blocked_marker_path(canonical: Path, task_id: str) -> Path:
    return ledger_of(canonical) / "health" / "blocked" / f"{task_id}.json"


def _outbox_has(canonical: Path, title: str) -> bool:
    for path in (Path(canonical) / OUTBOX_DIR).glob("*.json"):
        message = read_json(path)
        if isinstance(message, dict) and message.get("title") == title:
            return True
    return False


def _requeue(canonical: Path, queue: Queue, card: Card) -> BlockedAction:
    queue.requeue(card.task_id, "requeued once after a block (health)")
    marker = blocked_marker_path(canonical, card.task_id)
    with canonical_lock(canonical):
        write_json(
            marker,
            {
                "task_id": card.task_id,
                "request_id": card.spec.request_id,
                "state": REQUEUED,
                "requeued_at": now_iso(),
            },
        )
        commit(canonical, [marker], f"health: requeued blocked card {card.task_id}")
    return BlockedAction(card.task_id, REQUEUED)


def _repair(canonical: Path, card: Card, record: dict, allow_no_remote: bool) -> BlockedAction:
    task_id = card.task_id
    covered = promotion.covers_task(canonical, task_id)
    title = f"Card {task_id} is blocked again after a requeue"
    with canonical_lock(canonical):
        request_id = None
        if not covered:
            why = (
                f"Card {task_id} (request {card.spec.request_id}, {card.spec.title!r}) was "
                f"blocked by Hermes twice: an operational failure such as a circuit breaker or "
                f"bad credentials. Redo its work; recover anything useful from branch "
                f"card/{card.spec.request_id} if it exists."
            )
            spec = CardSpec(
                "",
                f"Repair blocked card {task_id}",
                card.spec.mode,
                why,
                list(card.spec.refs),
                RUNTIME,
                REPAIR_PRIORITY,
            )
            commit_runtime_request(canonical, repair_blocked_marker(task_id), spec)
            request_id = marked_request(canonical, repair_blocked_marker(task_id)).stem
        if not _outbox_has(canonical, title):
            follow_up = (
                f"Repair request {request_id} redoes its work."
                if request_id
                else "Its promotion's recovery card is the follow-up."
            )
            body = (
                f"Hermes blocked card {task_id} ({card.spec.title}) twice, once after the "
                f"runtime requeued it. {follow_up} Please check the Hermes logs for the cause "
                f"(credentials, circuit breaker) and fix it."
            )
            publish_runtime(canonical, "help_request", title, body, [], allow_no_remote)
        marker = blocked_marker_path(canonical, task_id)
        write_json(marker, record | {"state": REPAIRED, "repair_request": request_id})
        commit(canonical, [marker], f"health: blocked card {task_id} needs repair")
    return BlockedAction(task_id, REPAIRED, request_id)


def handle_blocked(
    ctx: RunContext, queue: Queue, allow_no_remote: bool = False
) -> list[BlockedAction]:
    """Requeue each blocked card once, then repair (module docstring). The caller must not
    hold the canonical lock."""
    canonical = ctx.canonical
    actions = []
    for card in queue.list("blocked"):
        record = committed_json(canonical, blocked_marker_path(canonical, card.task_id))
        if record is None:
            actions.append(_requeue(canonical, queue, card))
        elif record["state"] == REQUEUED:
            actions.append(_repair(canonical, card, record, allow_no_remote))
    return actions
