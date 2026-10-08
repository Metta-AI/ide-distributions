"""``reprioritize`` and ``drop`` card requests (Overview C3 ``op``). Runtime only:
queue-keeper applies them during admission.

A worker or planner writes ``cards/requests/<Q-id>.json`` with ``op: reprioritize``
(``target_request``, ``priority``) or ``op: drop`` (``target_request``, ``reason``); like a
create request, it takes effect only once committed in canonical (after its branch merges).

Each op request is applied once. Its outcome is the committed marker
``.supervisor/ledger/ops/<Q-id>.json`` ``{request_id, op, target_request, state, detail, at}``
with ``state`` ``applied`` or ``ignored`` (the target is not an admitted request whose card is
still pending: a claimed card is never re-prioritized or dropped under a worker). A drop also
moves the target's receipt to ``dropped`` in the same commit, which closes it for routing
(inbound goes back to the backlog) and releases backlog topics its card took.

Crash safety: the queue change comes first, then the commit. A rerun re-applies a
reprioritize (idempotent), and recognises an already dropped card (completed with outcome
``dropped``) as applied, so the receipt still becomes ``dropped``.
"""

from dataclasses import dataclass
from pathlib import Path

import jsonschema

from autoresearch.io.docs import write_json
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Card
from autoresearch.queue.requests import load_schema, read_requests
from autoresearch.runctx import RunContext
from autoresearch.runtime.admission import receipt_path
from autoresearch.runtime.ledger import (
    canonical_lock,
    commit,
    committed_json,
    ledger_of,
    now_iso,
)

OPS = ("reprioritize", "drop")
OPS_DIR = "ops"


@dataclass
class OpOutcome:
    request_id: str
    op: str
    target_request: str
    state: str  # applied | ignored
    detail: str


def op_marker_path(canonical: Path, request_id: str) -> Path:
    return ledger_of(canonical) / OPS_DIR / f"{request_id}.json"


def _target_card(queue: Queue, receipt: dict | None) -> Card | None:
    if receipt is None or receipt["state"] != "admitted" or not receipt.get("tasks"):
        return None
    try:
        return queue.get(receipt["tasks"][-1]["task_id"])
    except (KeyError, RuntimeError):  # FileQueue: unknown card; Kanban: show failed
        return None


def _already_dropped(card: Card | None) -> bool:
    return (
        card is not None
        and card.state == "completed"
        and bool(card.runs)
        and card.runs[-1].outcome == "dropped"
    )


def _apply(queue: Queue, request: dict, card: Card | None) -> tuple[str, str]:
    """(state, detail) after applying ``request`` to the target ``card``."""
    op = request["op"]
    if op == "drop" and _already_dropped(card):
        return "applied", "card already dropped"
    if card is None or card.state != "pending":
        state = "absent" if card is None else card.state
        return "ignored", f"target is not an admitted pending card ({state})"
    if op == "reprioritize":
        queue.reprioritize(card.task_id, request["priority"])
        return "applied", f"{card.task_id} -> {request['priority']}"
    queue.drop(card.task_id, request["reason"])
    return "applied", f"{card.task_id} dropped: {request['reason']}"


def _record(canonical: Path, request: dict, state: str, detail: str) -> None:
    """Write the op marker (and, for an applied drop, the dropped receipt) and commit them.
    Call under the canonical lock."""
    marker = op_marker_path(canonical, request["id"])
    paths = [marker]
    write_json(
        marker,
        {
            "request_id": request["id"],
            "op": request["op"],
            "target_request": request["target_request"],
            "state": state,
            "detail": detail,
            "at": now_iso(),
        },
    )
    target = receipt_path(ledger_of(canonical), request["target_request"])
    receipt = committed_json(canonical, target)
    if request["op"] == "drop" and state == "applied" and receipt["state"] == "admitted":
        dropped = receipt | {
            "state": "dropped",
            "reason": request["reason"],
            "history": [*receipt["history"], {"state": "dropped", "at": now_iso()}],
        }
        jsonschema.validate(dropped, load_schema("receipt.json"))
        write_json(target, dropped)
        paths.append(target)
    commit(canonical, paths, f"{request['op']} {request['target_request']} ({request['id']})")


def apply_ops(ctx: RunContext, queue: Queue) -> list[OpOutcome]:
    """Apply every committed reprioritize/drop request with no marker yet (module docstring).
    The caller must not hold the canonical lock."""
    canonical = ctx.canonical
    outcomes = []
    for request in read_requests(canonical, "HEAD"):
        if request.get("op") not in OPS:
            continue
        if committed_json(canonical, op_marker_path(canonical, request["id"])) is not None:
            continue
        target = receipt_path(ledger_of(canonical), request["target_request"])
        card = _target_card(queue, committed_json(canonical, target))
        state, detail = _apply(queue, request, card)
        with canonical_lock(canonical):
            _record(canonical, request, state, detail)
        outcomes.append(
            OpOutcome(request["id"], request["op"], request["target_request"], state, detail)
        )
    return outcomes
