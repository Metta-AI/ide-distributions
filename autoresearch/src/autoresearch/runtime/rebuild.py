"""Rebuild the work queue from the durable ledger after board loss (Overview C12; amendments
A2, A10, A11). Runtime only: queue-keeper calls ``needs_rebuild`` each run after preflight and
promotion reconcile, and ``rebuild`` when it says so.

Detection (``needs_rebuild``). Rebuild when:

- the board is missing (``queue.board_exists()`` is False; A2: its DB or list entry is
  gone; a queue without ``board_exists`` is taken to exist), or
- an earlier rebuild did not finish (``ledger/board.json`` has ``rebuilding: true``), or
- the ledger lists open requests and the board holds none of their tasks. Open: an
  ``admitted`` receipt, or a ``handed_off`` receipt whose handoff still needs processing.
  Requests a promotion covers (A11) do not count: their only follow-up is the promotion's
  recovery card, so a dead promotion holder never triggers rebuild after rebuild.

Rebuild (``rebuild``). Every step is idempotent, so a crash at any point is finished by the
next run (the unfinished-rebuild rule above brings it back, in the same epoch):

1. Board epoch: a new epoch (``e<n+1>``) in ``ledger/board.json`` with ``rebuilding: true``,
   committed. Tasks of earlier epochs are now gone: their committed handoffs are adopted by
   normal processing (C3 earlier-epoch rule), and promotion holders count as terminal.
2. ``queue.ensure_board()`` when the queue has it.
3. Exactly one recovery action per request receipt: ``admitted`` with no handoff ->
   re-admitted with key ``req:<Q-id>:<epoch>`` (``admission.readmit``; the request's spec is
   its committed ``cards/requests/<Q-id>.json``); ``handed_off`` -> nothing (normal
   processing adopts the handoff, and its repair or reconcile is the only follow-up);
   ``published|superseded|dropped|rejected`` -> nothing; covered by a promotion that reached
   submission (A11: holder, former holder, earlier recovery cards) -> nothing, except the
   latest recovery request of an in-flight promotion, which is re-admitted.
4. Experiment lifecycle cards, one per experiment whose next step no open request covers (an
   open request is committed, not closed, and refs ``ar://experiment/<E>``):
   ``designed`` -> build; ``built`` -> upload; ``uploaded`` with a ``candidate_binding`` ->
   launch; ``awaiting-credits`` -> launch, unless ``lifecycle.next_launch_after`` is still in
   the future (A10 backoff); ``evaluating`` whose XP requests (``xp_recover``) all completed
   -> review; ``reviewed`` with decision ``promote`` and no promotion of it -> promote.
   Launch markers are ``launch:<E>:<lifecycle.launch_attempts>`` (the xp-poll key, so the two
   dedupe), and the launch card says to run ``xp recover`` before any create, so a rebuilt
   launch never duplicates an XP request. Other markers carry the epoch
   (``build:<E>@<epoch>``), so a later rebuild can ask again.
5. ``promotion.reconcile`` (C8): with the new epoch a dead ``submitted`` holder gets its
   recovery request.
6. Calibration (C10): ``calibrate:<game_version>`` when no noise record is current.
7. One planning card (C11), marker ``planning:rebuild:<epoch>``, unless planning is owned.
8. ``board.json`` ``rebuilding: false``, committed.

New cards are runtime requests committed in canonical ``cards/requests/`` with their marker
in ``why`` (admission admits them); re-admitted requests already have their new task.
"""

import datetime
import logging
import re
from pathlib import Path

from ulid import ULID

from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion
from autoresearch.runtime.admission import (
    DEFAULT_BOARD_EPOCH,
    has_current_noise,
    readmit,
)
from autoresearch.runtime.handoff import handoff_path
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.merge import PROCESS_STATES
from autoresearch.runtime.planning import (
    RUNTIME,
    commit_runtime_request,
    request_planning,
)
from autoresearch.softmax_tool.protocol import Softmax

BOARD_FILE = "board.json"
OPEN_RECEIPT_STATES = ("admitted", "handed_off")
LIFECYCLE_PRIORITY = "P1"

log = logging.getLogger(__name__)


# --- board epoch ---


def _board_path(canonical: Path) -> Path:
    return ledger_of(canonical) / BOARD_FILE


def _board_file(canonical: Path) -> dict:
    path = _board_path(canonical)
    return read_json(path) if path.exists() else {"epoch": DEFAULT_BOARD_EPOCH}


def _next_epoch(epoch: str) -> str:
    match = re.fullmatch(r"e(\d+)", epoch)
    return f"e{int(match.group(1)) + 1}" if match else f"e-{ULID()}"


def _start_epoch(canonical: Path) -> str:
    """The rebuild's epoch: the unfinished one, or a new one (committed). Under the lock."""
    board = _board_file(canonical)
    if board.get("rebuilding"):
        return board["epoch"]
    epoch = _next_epoch(board["epoch"])
    record = {
        "epoch": epoch,
        "previous_epoch": board["epoch"],
        "rebuilding": True,
        "started_at": now_iso(),
    }
    write_json(_board_path(canonical), record)
    commit(canonical, [_board_path(canonical)], f"rebuild: board epoch {epoch}")
    return epoch


def _finish_epoch(canonical: Path) -> None:
    board = _board_file(canonical) | {"rebuilding": False, "rebuilt_at": now_iso()}
    write_json(_board_path(canonical), board)
    commit(canonical, [_board_path(canonical)], f"rebuild: board epoch {board['epoch']} done")


# --- receipts ---


def _receipts(canonical: Path) -> list[dict]:
    paths = sorted((ledger_of(canonical) / "requests").glob("Q-*.json"))
    return [read_json(path) for path in paths]


def _has_handoff(canonical: Path, receipt: dict) -> bool:
    return any(
        any(handoff_path(canonical, task["task_id"], "x").parent.glob("*.json"))
        for task in receipt.get("tasks", [])
    )


def _covered_by_promotion(canonical: Path, receipt: dict) -> bool:
    """A11: the request belongs to a promotion that reached submission, and is not the latest
    recovery request of the in-flight promotion (which rebuild re-admits)."""
    current = promotion.load(canonical)
    if (
        current is not None
        and current.stage in promotion.IN_FLIGHT
        and receipt["request_id"] == current.recovery_request
    ):
        return False
    return any(promotion.covers_task(canonical, t["task_id"]) for t in receipt.get("tasks", []))


def _is_open(canonical: Path, receipt: dict) -> bool:
    if receipt["state"] == "admitted":
        return not _covered_by_promotion(canonical, receipt)
    if receipt["state"] == "handed_off":
        handed = receipt["handed_off"]
        record = committed_json(
            canonical, handoff_path(canonical, handed["task_id"], handed["run_id"])
        )
        return record is not None and record["state"] in PROCESS_STATES
    return False


def needs_rebuild(ctx: RunContext, queue: Queue) -> bool:
    """C12 detection; see the module docstring."""
    canonical = ctx.canonical
    board_exists = getattr(queue, "board_exists", None)
    if board_exists is not None and not board_exists():
        return True
    if _board_file(canonical).get("rebuilding"):
        return True
    open_tasks = {
        task["task_id"]
        for receipt in _receipts(canonical)
        if _is_open(canonical, receipt)
        for task in receipt.get("tasks", [])
    }
    if not open_tasks:
        return False
    return not open_tasks & {card.task_id for card in queue.list()}


def _recover_requests(ctx: RunContext, queue: Queue, epoch: str) -> list[CardSpec]:
    """Step 3: re-admit each ``admitted`` request that has no handoff and is not covered."""
    canonical = ctx.canonical
    readmitted = []
    for receipt in _receipts(canonical):
        if receipt["state"] != "admitted" or receipt["tasks"][-1]["board_epoch"] == epoch:
            continue
        if _has_handoff(canonical, receipt) or _covered_by_promotion(canonical, receipt):
            continue
        request_id = receipt["request_id"]
        request = committed_json(canonical, Layout(canonical).card_requests / f"{request_id}.json")
        if not request or not request.get("spec"):
            log.warning("rebuild: request %s has no committed spec; not re-admitted", request_id)
            continue
        spec = CardSpec(**request["spec"])
        readmit(ctx, queue, spec)
        readmitted.append(spec)
    return readmitted


# --- experiment lifecycle ---


def _open_experiment_refs(canonical: Path) -> set[str]:
    """``ar://experiment/<E>`` refs of committed requests that are not closed."""
    ledger = ledger_of(canonical)
    refs: set[str] = set()
    for path in sorted(Layout(canonical).card_requests.glob("Q-*.json")):
        request = committed_json(canonical, path)
        if not request or request.get("op") != "create":
            continue
        receipt = committed_json(canonical, ledger / "requests" / path.name)
        if receipt is None or receipt["state"] in OPEN_RECEIPT_STATES:
            refs.update(
                ref for ref in request["spec"]["refs"] if ref.startswith("ar://experiment/")
            )
    return refs


def _promoted(canonical: Path, experiment: str) -> bool:
    for path in (Path(canonical) / promotion.HISTORY_DIR_REL).glob("P-*.json"):
        record = read_json(path)
        if record.get("experiment") == experiment and record.get("stage") != "aborted":
            return True
    return False


def _parse_time(value) -> datetime.datetime | None:
    if not isinstance(value, str):
        return value if isinstance(value, datetime.datetime) else None
    moment = datetime.datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.UTC)


def _xp_completed(sm: Softmax, experiment: str) -> bool:
    handles = sm.xp_recover(experiment)
    return bool(handles) and all(sm.xp_status(h).state == "completed" for h in handles)


def _launch(experiment: dict) -> tuple[str, str, str, str]:
    attempts = (experiment.get("lifecycle") or {}).get("launch_attempts", 0)
    eid = experiment["id"]
    why = (
        f"Launch the XP evaluation of {eid} (launch attempt {attempts + 1}; the board was "
        f'rebuilt). First run "$AUTORESEARCH_CLI" softmax xp recover {eid}: if it finds XP '
        f"requests, adopt them and do not create another. Create a request only when xp "
        f"recover finds none."
    )
    return f"launch:{eid}:{attempts}", f"Launch {eid}", why, "experimentation"


def _lifecycle_step(
    canonical: Path, experiment: dict, sm: Softmax, epoch: str, now: datetime.datetime
) -> tuple[str, str, str, str] | None:
    """(marker, title, why, mode) of the card that moves ``experiment`` on, or None."""
    eid, status = experiment["id"], experiment.get("status")
    if status == "designed":
        why = f"Experiment {eid} is designed but not built (board rebuilt). Build the candidate."
        return f"build:{eid}@{epoch}", f"Build {eid}", why, "experimentation"
    if status == "built":
        why = f"Experiment {eid} is built but not uploaded (board rebuilt). Upload the policy."
        return f"upload:{eid}@{epoch}", f"Upload {eid}", why, "experimentation"
    if status == "uploaded" and experiment.get("candidate_binding"):
        return _launch(experiment)
    if status == "awaiting-credits":
        after = _parse_time((experiment.get("lifecycle") or {}).get("next_launch_after"))
        return None if after is not None and now < after else _launch(experiment)
    if status == "evaluating" and _xp_completed(sm, eid):
        why = f"The XP requests of {eid} completed (board rebuilt). Review the results."
        return f"review:{eid}@{epoch}", f"Review {eid}", why, "experimentation"
    decision = experiment.get("decision") or {}
    if (
        status == "reviewed"
        and decision.get("outcome") == "promote"
        and not _promoted(canonical, eid)
    ):
        why = f"Experiment {eid} was reviewed with decision promote and has no promotion yet."
        return f"promote:{eid}@{epoch}", f"Promote {eid}", why, "maintenance"
    return None


def _lifecycle_cards(
    ctx: RunContext, sm: Softmax, epoch: str, now: datetime.datetime
) -> list[CardSpec]:
    """Step 4 (module docstring)."""
    canonical = ctx.canonical
    store = RecordStore(Layout(canonical))
    covered = _open_experiment_refs(canonical)
    created = []
    for experiment_id in store.find("experiment"):
        if f"ar://experiment/{experiment_id}" in covered:
            continue
        experiment, _ = store.load(experiment_id)
        step = _lifecycle_step(canonical, experiment, sm, epoch, now)
        if step is None:
            continue
        marker, title, why, mode = step
        refs = [f"ar://experiment/{experiment_id}"]
        binding = experiment.get("candidate_binding") or {}
        if binding.get("version_id"):
            refs.append(f"ar://version/{binding['version_id']}")
        spec = CardSpec("", title, mode, why, refs, RUNTIME, LIFECYCLE_PRIORITY)
        with canonical_lock(canonical):
            new = commit_runtime_request(canonical, marker, spec)
        if new is not None:
            created.append(new)
    return created


# --- promotion, calibration, planning ---


def _reconcile_promotion(ctx: RunContext, queue: Queue, sm: Softmax) -> list[CardSpec]:
    created = []
    for action in promotion.reconcile(ctx, queue, sm):
        if action.kind == "recovery_requested" and action.request_id:
            path = Layout(ctx.canonical).card_requests / f"{action.request_id}.json"
            created.append(CardSpec(**read_json(path)["spec"]))
    return created


def _calibration(ctx: RunContext, game_version: str) -> list[CardSpec]:
    canonical = ctx.canonical
    if has_current_noise(canonical, game_version):
        return []
    why = (
        f"No metric noise record for the current game version {game_version} (C10). Measure "
        f"metric noise from league episodes and write stats/metric-noise.yaml before any "
        f"experiment runs."
    )
    title = f"Calibrate metric noise for {game_version}"
    spec = CardSpec("", title, "maintenance", why, [], RUNTIME, "P1")
    with canonical_lock(canonical):
        new = commit_runtime_request(canonical, f"calibrate:{game_version}", spec)
    return [new] if new is not None else []


def rebuild(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    *,
    game_version: str,
    now: datetime.datetime | None = None,
) -> list[CardSpec]:
    """C12 rebuild (module docstring). ``game_version`` is the league's current game version
    (calibration); ``now`` (default: the current time) is for the A10 launch backoff.
    Returns the specs of the cards it created, re-admitted requests included. The caller
    must not hold the canonical lock."""
    canonical = ctx.canonical
    now = now or datetime.datetime.now(datetime.UTC)
    with canonical_lock(canonical):
        epoch = _start_epoch(canonical)
    ensure_board = getattr(queue, "ensure_board", None)
    if ensure_board is not None:
        ensure_board()
    created = _recover_requests(ctx, queue, epoch)
    created += _lifecycle_cards(ctx, sm, epoch, now)
    created += _reconcile_promotion(ctx, queue, sm)
    created += _calibration(ctx, game_version)
    planning = request_planning(
        ctx, queue, f"planning:rebuild:{epoch}", f"The board was rebuilt (epoch {epoch})."
    )
    if planning is not None:
        created.append(planning)
    with canonical_lock(canonical):
        _finish_epoch(canonical)
    return created
