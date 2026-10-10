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
module never sees it. That repeat is reported only by the agent's own help_request. A spend
stop can set this up: it blocks with ``needs_input`` when the card's previous block was
``capability`` (``KanbanQueue.stop``), so after a human unblocks it, the worker's own D-26
tool-fault block (also ``needs_input``) lands in ``triage``.

While the agent is paused (``pause.json``) or the coach holds research (``coach-hold.json``),
a card due for its requeue is left blocked: the requeue keeps the card's assignee, so it would
be dispatched. The first run after the resume requeues it. Repair requests are still written:
admission holds them until the resume.

The queue change comes before the commit, so a crash between them at worst requeues a card
a second time; it never skips straight to a repair.

A card whose handoff is already published (its request receipt is ``published``) cannot hand
off again: ``card finish`` refuses a closed request. Rerunning it would waste a whole run, so it
is never requeued. What happens depends on the outcome of that published handoff (the
receipt's ``handed_off`` run):

- ``blocked`` (the work is not done): the repair path at once, with one runtime ``note``
  (``card_repaired_after_publish``) instead of the blocked-twice help_request. A card blocked by
  Hermes (not a ``tool-fault:`` block, not at a cap) gets the repair request
  (``repair-blocked:<task-id>``) and stays blocked.
- ``done``, ``partial``, ``dropped`` or ``failed`` (the work is over): no repair and no message;
  a blocked card is left blocked, and a rerun is closed (below).

A ``tool-fault:`` block stays blocked until a human acts, as above (D-26). When the human
unblocks it, the card is ``pending`` (Hermes ``kanban unblock``: ``ready``) and soon ``claimed``
(the dispatcher takes it within seconds). Each run of ``handle_blocked`` also looks at pending and
claimed cards whose handoff is published: for a ``blocked`` one it writes the repair request
first, then it closes the card (``drop`` when pending, ``stop`` when running), so a crash between
the two leaves a card that the next run closes without a second request. A running card whose
current run already has a committed handoff is left alone (it was requeued before its earlier
handoff published, and its own handoff is on its way). A rerun that ends between two runs of this
step (its refused ``card finish``, then ``kanban_complete``) is not seen, and gets no repair.

Repair chains are bounded (``MAX_REPAIR_CHAIN``, both paths): the depth of a card is the number
of repairs that led to it, counted back over the committed markers (each names its card's
``request_id`` and its ``repair_request``). At the limit no repair is opened: one P1
``help_request`` asks a human (``repair_chain_limit``), the marker becomes ``{state: repaired,
repair_request: null, chain_limit: true}``, and the card stays blocked.

The repair card redoes the work, and its handoff completes the coach's task
(``publisher.acknowledge_inbound``: the follow-up acknowledgement of a ``blocked`` task).
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Card, CardSpec
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion, spend
from autoresearch.runtime.admission import receipt_path
from autoresearch.runtime.handoff import handoff_path
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.markers import (
    MAX_REPAIR_CHAIN,
    blocked_marker_path,
    repair_blocked_marker,
)
from autoresearch.runtime.pause import is_held, is_paused
from autoresearch.runtime.planning import RUNTIME, commit_runtime_request, marked_request

REQUEUED = "requeued"
REPAIRED = "repaired"
CHAIN_LIMIT = "chain-limit"  # a BlockedAction: at MAX_REPAIR_CHAIN, a help request, no repair
SPEND_CAP = "spend-cap"
TOOL_FAULT = "tool-fault"
TOOL_FAULT_PREFIX = "tool-fault:"
REPAIR_PRIORITY = "P1"
# The reason a card rerun after its published handoff is stopped with (``_close_rerun``).
RERUN_AFTER_PUBLISH = "handoff already published: a repair card redoes its work"


@dataclass
class BlockedAction:
    task_id: str
    action: str  # requeued | repaired | chain-limit | tool-fault | spend-cap
    repair_request: str | None = None


def _published_outcome(canonical: Path, card: Card) -> str | None:
    """The outcome of the card's request's latest handoff once the request receipt is
    ``published`` (no run can hand off again); None while the request is open. A receipt that
    names no readable handoff counts as final (``unknown``): nothing is redone."""
    receipt = committed_json(canonical, receipt_path(ledger_of(canonical), card.spec.request_id))
    if receipt is None or receipt["state"] != "published":
        return None
    handed_off = receipt.get("handed_off") or {}
    handoff = None
    if handed_off:
        path = handoff_path(canonical, handed_off["task_id"], handed_off["run_id"])
        handoff = committed_json(canonical, path)
    return str(handoff.get("outcome")) if handoff else "unknown"


def _repair_depth(canonical: Path, request_id: str) -> int:
    """How many repairs led to the card of ``request_id`` (0 for a card no repair made), up to
    ``MAX_REPAIR_CHAIN``: back over the committed blocked-card markers, each of which names its
    card's ``request_id`` and the ``repair_request`` written for it."""
    directory = blocked_marker_path(canonical, "x").parent.relative_to(canonical).as_posix()
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{directory}/")
    parent_of = {}
    for path in listing.splitlines():
        marker = committed_json(canonical, canonical / path) or {}
        if marker.get("repair_request"):
            parent_of[marker["repair_request"]] = marker.get("request_id")
    depth = 0
    while request_id in parent_of and depth < MAX_REPAIR_CHAIN:
        request_id = parent_of[request_id]
        depth += 1
    return depth


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


def _repair(
    canonical: Path,
    card: Card,
    record: dict | None,
    allow_no_remote: bool,
    after_publish: bool = False,
) -> BlockedAction:
    """One repair request and one runtime message, then the marker ``repaired``. Blocked
    twice by Hermes, or (``after_publish``) blocked or rerun after its ``blocked`` handoff was
    published. At ``MAX_REPAIR_CHAIN`` repairs deep, a help request instead of the repair."""
    task_id = card.task_id
    covered = promotion.covers_task(canonical, task_id)
    with canonical_lock(canonical):
        request_id = None
        depth = 0 if covered else _repair_depth(canonical, card.spec.request_id)
        at_limit = depth >= MAX_REPAIR_CHAIN
        if not covered and not at_limit:
            what = (
                "was blocked or put back in the queue after its handoff was published, so it "
                "cannot hand off again"
                if after_publish
                else "was blocked by Hermes twice: an operational failure such as a circuit "
                "breaker or bad credentials"
            )
            why = (
                f"Card {task_id} (request {card.spec.request_id}, {card.spec.title!r}) {what}. "
                f"Redo its work; recover anything useful from branch "
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
        args = (card.spec.title, task_id, card.spec.request_id, request_id, _refs(card))
        if at_limit:
            message = runtime_messages.repair_chain_limit(
                card.spec.title, task_id, card.spec.request_id, MAX_REPAIR_CHAIN, _refs(card)
            )
            fields = {"priority": REPAIR_PRIORITY, "expects_response": True}
        elif after_publish:
            message = runtime_messages.card_repaired_after_publish(*args)
            fields = {}
        else:
            message = runtime_messages.card_blocked_twice(*args)
            fields = {"priority": REPAIR_PRIORITY, "expects_response": True}
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote, **fields)
        marker = blocked_marker_path(canonical, task_id)
        base = record or {"task_id": task_id, "request_id": card.spec.request_id}
        repaired = {"state": REPAIRED, "repair_request": request_id}
        if at_limit:
            repaired["chain_limit"] = True
        write_json(marker, base | repaired)
        commit(canonical, [marker], f"health: blocked card {task_id} needs repair")
    return BlockedAction(task_id, CHAIN_LIMIT if at_limit else REPAIRED, request_id)


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


UNREADABLE = "state.db unreadable"


def _cap_reached(ctx: RunContext, card: Card, config: spend.SpendConfig) -> str | None:
    """``spend.cap_reached``, or ``UNREADABLE`` when ``state.db`` cannot be read: the
    conservative choice is to leave the card alone this run, not to requeue it."""
    try:
        return spend.cap_reached(ctx, card, config)
    except sqlite3.Error:
        return UNREADABLE


def _handle_one(
    ctx: RunContext,
    queue: Queue,
    card: Card,
    allow_no_remote: bool,
    spend_config: spend.SpendConfig | None,
) -> BlockedAction | None:
    canonical = ctx.canonical
    if spend.is_held(canonical, card):
        return None
    record = committed_json(canonical, blocked_marker_path(canonical, card.task_id))
    reason = card.block_reason or ""
    if reason.startswith(TOOL_FAULT_PREFIX):
        reported = (
            record is not None
            and record["state"] == TOOL_FAULT
            and record.get("block_run_id") == card.block_run_id
        )
        return None if reported else _tool_fault(canonical, card, reason, allow_no_remote)
    outcome = _published_outcome(canonical, card)
    if outcome not in (None, "blocked"):
        return None  # its work is over and published: nothing to redo, and a rerun is refused
    if (
        spend_config is not None
        and (record is None or record["state"] in (TOOL_FAULT, REQUEUED))
        and (why := _cap_reached(ctx, card, spend_config))
    ):
        if why is UNREADABLE:
            return None  # state.db unreadable: neither requeue nor hold this run
        spend.hold_blocked(ctx, card, why, spend_config, allow_no_remote)
        return BlockedAction(card.task_id, SPEND_CAP)
    if record is None or record["state"] == TOOL_FAULT:
        if outcome == "blocked":
            return _repair(canonical, card, record, allow_no_remote, after_publish=True)
        ledger = ledger_of(canonical)
        if is_paused(ledger) or is_held(ledger):
            return None  # a requeue keeps the assignee, so it would dispatch: wait for resume
        return _requeue(canonical, queue, card)
    if record["state"] == REQUEUED:
        return _repair(canonical, card, record, allow_no_remote)
    return None


def _close_rerun(
    ctx: RunContext, queue: Queue, card: Card, allow_no_remote: bool
) -> BlockedAction | None:
    """A pending or running card whose handoff is published (a human unblocked it, or Hermes
    ran it again): it cannot hand off, so it is closed; when that handoff was ``blocked``, the
    repair request comes first. A promotion's card is left to its promotion; a running card in a
    status the queue cannot stop is left for a later run, and one whose current run already
    handed off is left to finish."""
    canonical = ctx.canonical
    outcome = _published_outcome(canonical, card)
    if outcome is None or promotion.covers_task(canonical, card.task_id):
        return None
    if card.state == "claimed":
        if card.board_status not in (None, "running"):
            return None
        run = card.current_run_id
        if run and committed_json(canonical, handoff_path(canonical, card.task_id, run)):
            return None
    record = committed_json(canonical, blocked_marker_path(canonical, card.task_id))
    action = None
    if outcome == "blocked" and (record is None or record["state"] != REPAIRED):
        action = _repair(canonical, card, record, allow_no_remote, after_publish=True)
    if card.state == "pending":
        queue.drop(card.task_id, RERUN_AFTER_PUBLISH)
    else:
        queue.stop(card.task_id, RERUN_AFTER_PUBLISH)
    return action


def handle_blocked(
    ctx: RunContext,
    queue: Queue,
    allow_no_remote: bool = False,
    spend_config: spend.SpendConfig | None = None,
) -> list[BlockedAction]:
    """Requeue each blocked card once, then repair; a ``tool-fault:`` block only asks for
    help; cards at a spend cap are left to the caps; a card whose ``blocked`` handoff is
    already published is repaired instead of rerun, and a rerun of a card whose handoff is
    published is closed (module docstring).
    The caller must not hold the canonical lock.

    One card never stops the others: a card whose handling fails (the board refuses the
    unblock, say) is left for the next run, and once every card was tried the failures are
    raised together, so the caller logs the step as failed (the actions of that run are then
    lost to the caller; their markers are committed)."""
    actions, failures = [], []
    cards = queue.list()
    handlers = [(card, _close_rerun, ()) for card in cards if card.state in ("pending", "claimed")]
    handlers += [(card, _handle_one, (spend_config,)) for card in cards if card.state == "blocked"]
    for card, handler, extra in handlers:
        try:
            action = handler(ctx, queue, card, allow_no_remote, *extra)
        except Exception as error:  # noqa: BLE001 - per-card containment, raised below
            failures.append(f"{card.task_id}: {type(error).__name__}: {error}")
            continue
        if action is not None:
            actions.append(action)
    if failures:
        raise RuntimeError("blocked card(s) not handled: " + "; ".join(failures))
    return actions
