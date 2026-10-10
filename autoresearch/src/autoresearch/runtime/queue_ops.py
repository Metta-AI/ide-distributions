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
``dropped``, or archived: ``KanbanQueue.drop``) as applied, so the receipt still becomes
``dropped``.

Failure containment: one op that raises never stops the others or fails the queue-keeper
step. Its failure is counted in ``.supervisor/runtime/op_failures.json``
(``{request_id: {runs, since, error}}``) and the op is retried next run. After
``OP_FAILURE_RUNS`` consecutive failing runs the runtime gives up on it: the marker is
committed with ``state: failed`` (so it is never retried) and one runtime ``help_request``
(``runtime_key`` ``queue-op-failed:<Q-id>``) says what failed and, for a drop, how a human
archives the card. A ``failed`` drop is looked at again every run: once its card is archived
(by a human) or otherwise dropped, the marker becomes ``applied`` and the receipt
``dropped``. Until then rebuild does not re-admit an archived card either
(``rebuild._classify``), so neither a human archive nor a crash between the archive and this
module's commit brings the card back. On 2026-10-09 on the box, every run failed the whole step on one drop
that Hermes refused (``complete`` of a child whose parent was blocked).
"""

from dataclasses import dataclass
from pathlib import Path

import jsonschema

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Card
from autoresearch.queue.requests import load_schema, read_requests
from autoresearch.runctx import BOARD, RunContext, runtime_dir
from autoresearch.runtime import provenance
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
OP_FAILURES_FILE = "op_failures.json"
OP_FAILURE_RUNS = 3  # consecutive failing runs before the runtime gives up on an op


@dataclass
class OpOutcome:
    request_id: str
    op: str
    target_request: str
    state: str  # applied | ignored | failed (given up, committed) | retrying (not committed)
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
    """Completed with outcome ``dropped``, or archived (how ``KanbanQueue.drop`` closes a
    card Hermes will not complete)."""
    if card is None or card.state != "completed":
        return False
    if card.board_status == "archived":
        return True
    return bool(card.runs) and card.runs[-1].outcome == "dropped"


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


def _read_failures(ctx: RunContext) -> dict:
    try:
        failures = read_json(runtime_dir(ctx) / OP_FAILURES_FILE)
    except (OSError, ValueError):
        return {}
    return failures if isinstance(failures, dict) else {}


def _give_up(
    ctx: RunContext, request: dict, card: Card | None, entry: dict, allow_no_remote: bool
) -> None:
    """Commit the ``failed`` marker and publish the one help_request (module docstring)."""
    canonical = ctx.canonical
    message = runtime_messages.queue_op_failed(
        request["op"],
        card.spec.title if card is not None else None,
        entry["runs"],
        entry["error"],
        request["id"],
        card.task_id if card is not None else None,
        BOARD,
    )
    with canonical_lock(canonical):
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote)
        detail = f"gave up after {entry['runs']} failing runs: {entry['error']}"
        _record(canonical, request, "failed", detail)


def _apply_one(
    ctx: RunContext, queue: Queue, request: dict, failures: dict, allow_no_remote: bool
) -> OpOutcome:
    """Apply and record one op; contain and count its failure (module docstring)."""
    canonical = ctx.canonical
    target = receipt_path(ledger_of(canonical), request["target_request"])

    def outcome(state: str, detail: str) -> OpOutcome:
        return OpOutcome(request["id"], request["op"], request["target_request"], state, detail)

    card = None
    try:
        card = _target_card(queue, committed_json(canonical, target))
        state, detail = _apply(queue, request, card)
        with canonical_lock(canonical):
            _record(canonical, request, state, detail)
    except Exception as error:  # per-op containment (module docstring)
        if provenance.journal_error(error) is not None:
            raise  # not an op failure: apply_ops raises it after the other ops
        previous = failures.get(request["id"]) or {"runs": 0, "since": now_iso()}
        entry = {**previous, "runs": int(previous["runs"]) + 1}
        entry["error"] = f"{type(error).__name__}: {error}"
        failures[request["id"]] = entry
        if entry["runs"] < OP_FAILURE_RUNS:
            return outcome("retrying", f"failing run {entry['runs']}: {entry['error']}")
        try:
            _give_up(ctx, request, card, entry, allow_no_remote)
        except Exception as problem:  # give up again next run
            if provenance.journal_error(problem) is not None:
                raise
            return outcome("retrying", f"{entry['error']}; could not give up: {problem}")
        failures.pop(request["id"])
        return outcome("failed", entry["error"])
    failures.pop(request["id"], None)
    return outcome(state, detail)


def _settle_failed_drop(ctx: RunContext, queue: Queue, request: dict) -> OpOutcome | None:
    """A drop the runtime gave up on whose card is now dropped (a human archived it, as the
    help_request asks): record it ``applied`` and the receipt ``dropped``. None while the
    card is still there, or when the check fails (tried again next run)."""
    canonical = ctx.canonical
    target = receipt_path(ledger_of(canonical), request["target_request"])
    try:
        receipt = committed_json(canonical, target)
        if not _already_dropped(_target_card(queue, receipt)):
            return None
        detail = "card dropped (archived) after the runtime gave up"
        with canonical_lock(canonical):
            _record(canonical, request, "applied", detail)
    except Exception as error:  # per-op containment; checked again next run
        if provenance.journal_error(error) is not None:
            raise
        return None
    return OpOutcome(request["id"], "drop", request["target_request"], "applied", detail)


def apply_ops(ctx: RunContext, queue: Queue, allow_no_remote: bool = False) -> list[OpOutcome]:
    """Apply every committed reprioritize/drop request with no marker yet (module docstring).
    One op's failure is contained and counted; it never raises out of here for a queue or
    board error. The caller must not hold the canonical lock."""
    canonical = ctx.canonical
    failures = _read_failures(ctx)
    outcomes = []
    pending = []
    journal = None
    for request in read_requests(canonical, "HEAD"):
        if request.get("op") not in OPS:
            continue
        marker = committed_json(canonical, op_marker_path(canonical, request["id"]))
        try:
            if marker is not None:
                if marker["state"] == "failed" and request["op"] == "drop":
                    settled = _settle_failed_drop(ctx, queue, request)
                    if settled is not None:
                        outcomes.append(settled)
                continue
            pending.append(request["id"])
            outcomes.append(_apply_one(ctx, queue, request, failures, allow_no_remote))
        except provenance.JournalError as error:  # the other ops still run; raised below
            journal = journal or error
    # Forget the counts of ops that are settled or gone.
    failures = {key: value for key, value in failures.items() if key in pending}
    path = runtime_dir(ctx) / OP_FAILURES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, failures)
    if journal is not None:  # typed, so queue-keeper fails closed
        raise journal
    return outcomes
