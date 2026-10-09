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

A card the worker blocked with a reason starting ``tool-fault:`` (doctrine D-26: a broken
binding, CLI or gate) skips all of that. A requeue or repair would redo the same broken
step, so the card is never requeued and gets no repair request. It gets one runtime
``help_request`` quoting the reason (the block's run is in its ``about``), and the marker becomes
``{state: tool-fault, block_run_id}``; the card stays blocked for a human. The marker is
keyed on the run, so a later tool-fault block (another run) is reported again. The agent also
sends its own help_request, which reaches canonical with its handoff (``card finish`` runs
before ``kanban_block``); this one covers the runtime's view and a worker that crashed. If a
human unblocks the card and a later non-tool-fault block follows, the marker is ignored and
the card follows the ordinary requeue-then-repair path.

Spend caps (``runtime.spend``) come first. A card stopped at a cap (reason starting
``spend-cap:``, or held at a cap) is skipped: never requeued, never repaired. With a
``spend_config``, a card that would be requeued or repaired but has reached its run cap or
its estimated-cost cap is held instead (``spend.hold_blocked``: one runtime
``help_request``; the card stays blocked for a human).

Hermes limit: a second ``kanban_block`` with the same kind after an unblock (D-26 uses
``needs_input``) is routed to ``triage``, which the Queue reports as ``pending``, so this
module never sees it. That repeat is reported only by the agent's own help_request.

The queue change comes before the commit, so a crash between them at worst requeues a card
a second time; it never skips straight to a repair.
"""

from dataclasses import dataclass
from pathlib import Path

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io.docs import write_json
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Card, CardSpec
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion, spend
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.markers import repair_blocked_marker
from autoresearch.runtime.planning import RUNTIME, commit_runtime_request, marked_request

REQUEUED = "requeued"
REPAIRED = "repaired"
SPEND_CAP = "spend-cap"
TOOL_FAULT = "tool-fault"
TOOL_FAULT_PREFIX = "tool-fault:"
REPAIR_PRIORITY = "P1"


@dataclass
class BlockedAction:
    task_id: str
    action: str  # requeued | repaired | tool-fault | spend-cap
    repair_request: str | None = None


def blocked_marker_path(canonical: Path, task_id: str) -> Path:
    return ledger_of(canonical) / "health" / "blocked" / f"{task_id}.json"


def _refs(card: Card) -> list[dict]:
    """The card's own refs: what its work is about."""
    return [{"uri": uri} for uri in card.spec.refs]


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
        message = runtime_messages.card_blocked_twice(
            card.spec.title, task_id, card.spec.request_id, request_id, _refs(card)
        )
        if not already_published(canonical, message):
            publish_message(
                canonical,
                message,
                allow_no_remote,
                priority=REPAIR_PRIORITY,
                expects_response=True,
            )
        marker = blocked_marker_path(canonical, task_id)
        write_json(marker, record | {"state": REPAIRED, "repair_request": request_id})
        commit(canonical, [marker], f"health: blocked card {task_id} needs repair")
    return BlockedAction(task_id, REPAIRED, request_id)


def _tool_fault(canonical: Path, card: Card, reason: str, allow_no_remote: bool) -> BlockedAction:
    task_id = card.task_id
    message = runtime_messages.card_tool_fault(
        card.spec.title, reason, task_id, card.spec.request_id, str(card.block_run_id), _refs(card)
    )
    with canonical_lock(canonical):
        if not already_published(canonical, message):
            publish_message(
                canonical,
                message,
                allow_no_remote,
                priority=REPAIR_PRIORITY,
                expects_response=True,
            )
        marker = blocked_marker_path(canonical, task_id)
        write_json(
            marker,
            {
                "task_id": task_id,
                "request_id": card.spec.request_id,
                "state": TOOL_FAULT,
                "block_run_id": card.block_run_id,
                "reason": reason,
            },
        )
        commit(canonical, [marker], f"health: card {task_id} blocked by a tool fault")
    return BlockedAction(task_id, TOOL_FAULT)


def handle_blocked(
    ctx: RunContext,
    queue: Queue,
    allow_no_remote: bool = False,
    spend_config: spend.SpendConfig | None = None,
) -> list[BlockedAction]:
    """Requeue each blocked card once, then repair; a ``tool-fault:`` block only asks for
    help; cards at a spend cap are left to the caps (module docstring). The caller must not
    hold the canonical lock."""
    canonical = ctx.canonical
    actions = []
    for card in queue.list("blocked"):
        if spend.is_held(canonical, card):
            continue
        record = committed_json(canonical, blocked_marker_path(canonical, card.task_id))
        reason = card.block_reason or ""
        if reason.startswith(TOOL_FAULT_PREFIX):
            reported = (
                record is not None
                and record["state"] == TOOL_FAULT
                and record.get("block_run_id") == card.block_run_id
            )
            if not reported:
                actions.append(_tool_fault(canonical, card, reason, allow_no_remote))
        elif (
            spend_config is not None
            and (record is None or record["state"] in (TOOL_FAULT, REQUEUED))
            and (why := spend.cap_reached(ctx, card, spend_config))
        ):
            spend.hold_blocked(ctx, card, why, spend_config, allow_no_remote)
            actions.append(BlockedAction(card.task_id, SPEND_CAP))
        elif record is None or record["state"] == TOOL_FAULT:
            actions.append(_requeue(canonical, queue, card))
        elif record["state"] == REQUEUED:
            actions.append(_repair(canonical, card, record, allow_no_remote))
    return actions
