"""Admission of card requests and the C3 create transaction (Overview C3, C7, C10, C11).

The receipt ``.supervisor/ledger/requests/<Q-id>.json`` is the request's durable state
machine; ``admit_and_create`` drives it to ``admitted`` and repairs any crash from it:

1. ``admit()`` checks the request (under the canonical lock; local reads only).
2. ``queue.create_unassigned`` with idempotency key ``req:<Q-id>`` (outside the lock). A
   crash here is repaired by the rerun: the key returns the same task.
3. Under the lock: write the ``admitted`` receipt with ``{task_id, board_epoch}`` and commit
   it (``admit <Q-id>``) — the durable point — then write the disposable
   ``runtime/cards/<task-id>.json`` (with ``responsible_inbound``, kept across rewrites and
   merged with the inbound routed to the request) and ``runtime/briefings/<task-id>.md``.
4. ``queue.assign(task_id, profile)`` (outside the lock). A rerun on an ``admitted`` receipt
   redoes the runtime files and this step.

Re-admission after a board loss (C12). An ``admitted`` receipt whose latest task is from an
earlier board epoch is not finished here (``STALE_EPOCH``; the card it names is gone):
``rebuild`` decides whether the request gets a new card and calls ``readmit``, which creates
it with the key ``req:<Q-id>:<epoch>``, appends ``{task_id, board_epoch}`` to ``tasks`` and
commits (``readmit <Q-id>``). After that, ``admit_and_create`` finishes steps 3-4 as usual.

Rejections for ``TRANSIENT_REASONS`` write no receipt, so the request is retried on a later
run (after calibration lands, or after resume; a coach hold rejects as ``paused`` too). Any
other rejection is terminal: a ``rejected`` receipt (``reject <Q-id>``). ``planning_owned`` is terminal because the caller
moves the request into the planning backlog. A terminal rejection on a rerun after a crash
between step 2 and the receipt also drops the unassigned task step 2 left on the board.

Briefing inputs may be a function of the task id (queue-keeper builds them per card, after
the task exists).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import yaml

from autoresearch.comms.delivery import responsible_ids
from autoresearch.comms.resolver import ResolveError, resolve
from autoresearch.games.loader import Game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json, write_text
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import REQUESTS_DIR, load_schema
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.briefing import BriefingInputs, build_briefing
from autoresearch.runtime.handoff import handoff_path
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.pause import is_held, is_paused

NOISE_PATH = "stats/metric-noise.yaml"
DEFAULT_BOARD_EPOCH = "e0"
# Receipt states in which a planning request still owns planning (C11): pending, claimed, or
# finished but not yet published.
PLANNING_OWNING_STATES = ("admitted", "handed_off")
NEEDS_CALIBRATION = "needs_calibration"
PLANNING_OWNED = "planning_owned"
PAUSED = "paused"
STALE_EPOCH = "stale_epoch"
TRANSIENT_REASONS = frozenset({NEEDS_CALIBRATION, PAUSED})
HANDOFF_REF = "ar://handoff/"

Inputs = BriefingInputs | Callable[[str], BriefingInputs]


@dataclass
class AdmissionResult:
    ok: bool
    task_id: str | None = None
    reasons: list[str] = field(default_factory=list)


def receipt_path(ledger: Path, request_id: str) -> Path:
    return ledger / "requests" / f"{request_id}.json"


def admitted_spec(canonical: Path, request_id: str) -> dict | None:
    """The spec of ``request_id``'s committed create request (``cards/requests/<Q-id>.json``),
    or None. Provenance: admission (and ``readmit``) only ever acts on a committed create
    request, so a receipt whose request has none was not written by the runtime. It is the
    ledger's answer to "did admission create this card?", independent of the board."""
    request = committed_json(canonical, canonical / REQUESTS_DIR / f"{request_id}.json")
    if not isinstance(request, dict) or request.get("op") != "create":
        return None
    return request.get("spec") or None


def _unresolved_refs(canonical: Path, spec: CardSpec) -> list[str]:
    reasons = []
    for uri in spec.refs:
        try:
            resolve(canonical, uri)
        except ResolveError as error:
            reasons.append(f"unresolved ref {uri}: {error}")
    return reasons


def has_current_noise(canonical: Path, game_version: str) -> bool:
    """True if ``stats/metric-noise.yaml`` at HEAD is for ``game_version`` (C10)."""
    content = gitops.show(canonical, "HEAD", NOISE_PATH)
    if content is None:
        return False
    record = yaml.safe_load(content)
    return (
        isinstance(record, dict)
        and str(record.get("game_version")).strip() == str(game_version).strip()
    )


def _planning_owned(canonical: Path, spec: CardSpec, queue: Queue) -> bool:
    """True if another planning card is pending, claimed, or finished-unpublished."""
    for state in ("pending", "claimed"):
        for card in queue.list(state):
            if card.spec.mode == "planning" and card.spec.request_id != spec.request_id:
                return True
    requests = ledger_of(canonical) / "requests"
    for path in requests.glob("Q-*.json"):
        receipt = read_json(path)
        if (
            receipt["request_id"] != spec.request_id
            and receipt.get("mode") == "planning"
            and receipt["state"] in PLANNING_OWNING_STATES
        ):
            return True
    return False


def admit(
    canonical: Path, spec: CardSpec, queue: Queue, game: Game, game_version: str
) -> list[str]:
    """Reasons to reject ``spec`` (empty: admissible). Checks: not paused (C7) or held by the
    coach (D15); every ref
    resolves at canonical HEAD; experimentation has a noise record for ``game_version`` (C10);
    planning has no other owner (C11), unless it is a runtime follow-up of a card's handoff.
    ``game`` is for game-declared ref types (Task 3.5)."""
    del game  # used once the resolver supports game reference types
    if is_paused(ledger_of(canonical)) or is_held(ledger_of(canonical)):
        return [PAUSED]
    reasons = _unresolved_refs(canonical, spec)
    if reasons:
        return reasons
    if spec.mode == "experimentation" and not has_current_noise(canonical, game_version):
        return [NEEDS_CALIBRATION]
    if (
        spec.mode == "planning"
        and not is_runtime_follow_up(canonical, spec)
        and _planning_owned(canonical, spec, queue)
    ):
        return [PLANNING_OWNED]
    return []


def is_runtime_follow_up(canonical: Path, spec: CardSpec) -> bool:
    """A repair/reconcile request the runtime wrote for an existing card's handoff. It
    carries on that card's work, so for a planning card it continues the same planning
    ownership (C11) instead of competing for it.

    Decided by provenance, never by the request's own fields (a worker can copy those): one
    of its ``ar://handoff/<task>/<run>`` refs names a handoff record, or for a run that left
    none the runtime's orphan marker, committed at canonical HEAD whose
    ``follow_up_request`` is this request."""
    ledger = ledger_of(canonical)
    for ref in spec.refs:
        if not ref.startswith(HANDOFF_REF):
            continue
        target, _, fragment = ref.removeprefix(HANDOFF_REF).partition("#")
        task_id, _, run_id = target.partition("/")
        if not task_id or not run_id:
            continue
        record_path = (
            ledger / "orphans" / f"{task_id}-{run_id}.json"
            if fragment
            else handoff_path(canonical, task_id, run_id)
        )
        record = committed_json(canonical, record_path)
        if record is not None and record.get("follow_up_request") == spec.request_id:
            return True
    return False


def board_epoch(ledger: Path) -> str:
    """The current board epoch from ``ledger/board.json`` (C1, C12)."""
    path = ledger / "board.json"
    return read_json(path)["epoch"] if path.exists() else DEFAULT_BOARD_EPOCH


def _save_receipt(canonical: Path, path: Path, receipt: dict, message: str) -> None:
    """Validate, write and commit a receipt; call under the canonical lock."""
    jsonschema.validate(receipt, load_schema("receipt.json"))
    write_json(path, receipt)
    commit(canonical, [path], message)


def _write_runtime_files(
    ctx: RunContext, spec: CardSpec, task_id: str, inputs: Inputs, pending: bool
) -> None:
    """Write ``runtime/cards/<task>.json`` and the briefing. ``finished`` and
    ``responsible_inbound`` already in the card file are kept; the inbound routed to this
    request is added, and, for a planning card not yet claimed (``pending``), the backlog
    no card has taken (C11). A claimed card never gains backlog it could not have seen."""
    runtime = runtime_dir(ctx)
    card_path = runtime / "cards" / f"{task_id}.json"
    existing = read_json(card_path) if card_path.exists() else {}
    routed = responsible_ids(
        ctx.canonical, task_id, spec.request_id, planning=pending and spec.mode == "planning"
    )
    responsible = list(existing.get("responsible_inbound", []))
    responsible += [inbound_id for inbound_id in routed if inbound_id not in responsible]
    write_json(
        card_path,
        {
            "task_id": task_id,
            "request_id": spec.request_id,
            "mode": spec.mode,
            "refs": spec.refs,
            "finished": existing.get("finished", False),
            "responsible_inbound": responsible,
        },
    )
    if callable(inputs):
        inputs = inputs(task_id)
    briefing = build_briefing(ctx.canonical, spec, task_id, inputs)
    write_text(runtime / "briefings" / f"{task_id}.md", briefing)


def _drop_orphans(queue: Queue, spec: CardSpec, reason: str) -> None:
    """Drop the pending cards of ``spec``'s request: a crash after ``create_unassigned``
    and before the receipt left one, and the request is now rejected for good."""
    for card in queue.list("pending"):
        if card.spec.request_id == spec.request_id:
            queue.drop(card.task_id, f"request rejected: {reason}")


def _reject(
    ctx: RunContext, queue: Queue, path: Path, spec: CardSpec, reasons: list[str]
) -> AdmissionResult:
    if not TRANSIENT_REASONS.intersection(reasons):
        receipt = {
            "request_id": spec.request_id,
            "state": "rejected",
            "mode": spec.mode,
            "reason": "; ".join(reasons),
            "history": [{"state": "rejected", "at": now_iso()}],
        }
        with canonical_lock(ctx.canonical):
            _save_receipt(ctx.canonical, path, receipt, f"reject {spec.request_id}")
        _drop_orphans(queue, spec, receipt["reason"])
    return AdmissionResult(ok=False, task_id=None, reasons=reasons)


def admit_and_create(
    ctx: RunContext,
    queue: Queue,
    spec: CardSpec,
    profile: str,
    inputs: Inputs,
    game: Game,
    game_version: str,
) -> AdmissionResult:
    """Run (or finish after a crash) the C3 admission transaction for one create request.

    The caller must not hold the canonical lock. Safe to rerun at any point."""
    canonical, ledger = ctx.canonical, ledger_of(ctx.canonical)
    path = receipt_path(ledger, spec.request_id)
    if is_paused(ledger) or is_held(ledger):
        return AdmissionResult(ok=False, reasons=[PAUSED])
    receipt = read_json(path) if path.exists() else None

    if receipt is None:
        with canonical_lock(canonical):
            reasons = admit(canonical, spec, queue, game, game_version)
        if reasons:
            return _reject(ctx, queue, path, spec, reasons)
        task_id = queue.create_unassigned(
            spec,
            branch=f"card/{spec.request_id}",
            worktree=canonical / ".worktrees" / spec.request_id,
        )
        receipt = {
            "request_id": spec.request_id,
            "state": "admitted",
            "mode": spec.mode,
            "tasks": [{"task_id": task_id, "board_epoch": board_epoch(ledger)}],
            "history": [{"state": "admitted", "at": now_iso()}],
        }
        with canonical_lock(canonical):
            _save_receipt(canonical, path, receipt, f"admit {spec.request_id}")
    elif receipt["state"] == "rejected":
        return AdmissionResult(ok=False, reasons=[receipt["reason"]])

    if receipt["state"] != "admitted":
        tasks = receipt.get("tasks", [])
        return AdmissionResult(ok=True, task_id=tasks[-1]["task_id"] if tasks else None)
    task_id = receipt["tasks"][-1]["task_id"]
    if receipt["tasks"][-1]["board_epoch"] != board_epoch(ledger):
        return AdmissionResult(ok=False, task_id=task_id, reasons=[STALE_EPOCH])
    card = queue.get(task_id)
    with canonical_lock(canonical):
        _write_runtime_files(ctx, spec, task_id, inputs, pending=card.state == "pending")
    if card.state == "pending" and card.assignee != profile:
        queue.assign(task_id, profile)
    return AdmissionResult(ok=True, task_id=task_id)


def readmit(ctx: RunContext, queue: Queue, spec: CardSpec) -> str | None:
    """C12 re-admission: give an ``admitted`` request whose latest task is from an earlier
    board epoch a new card (key ``req:<Q-id>:<epoch>``) and append it to the receipt's
    ``tasks``. Returns the current-epoch task id (also when an earlier call already did
    this), or None if the receipt is not ``admitted``. ``admit_and_create`` then writes the
    runtime files and assigns the card. A crash between the create and the commit is
    repaired by the rerun: the key returns the same task. The caller must not hold the
    canonical lock."""
    canonical, ledger = ctx.canonical, ledger_of(ctx.canonical)
    path = receipt_path(ledger, spec.request_id)
    epoch = board_epoch(ledger)
    receipt = read_json(path) if path.exists() else None
    if receipt is None or receipt["state"] != "admitted":
        return None
    if receipt["tasks"][-1]["board_epoch"] == epoch:
        return receipt["tasks"][-1]["task_id"]
    task_id = queue.create_unassigned(
        spec,
        branch=f"card/{spec.request_id}",
        worktree=canonical / ".worktrees" / spec.request_id,
        idempotency_key=f"req:{spec.request_id}:{epoch}",
    )
    with canonical_lock(canonical):
        receipt = read_json(path)
        receipt["tasks"].append({"task_id": task_id, "board_epoch": epoch})
        receipt["history"].append({"state": "admitted", "at": now_iso()})
        _save_receipt(canonical, path, receipt, f"readmit {spec.request_id} ({epoch})")
    return task_id
