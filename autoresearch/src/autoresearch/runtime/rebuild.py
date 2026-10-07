"""Rebuild the work queue from the durable ledger after board loss (Overview C12; amendments
A2, A10, A11). Runtime only: queue-keeper calls ``needs_rebuild`` each run after preflight and
promotion reconcile, and ``rebuild`` when it says so.

Detection (``needs_rebuild``). Rebuild when:

- the board is missing (``queue.board_exists()`` is False; A2: its DB or list entry is
  gone; a queue without ``board_exists`` is taken to exist), or
- an earlier rebuild did not finish (``ledger/board.json`` has ``rebuilding: true``), or
- the ledger lists open requests and the board holds none of their tasks. Open is exactly
  what rebuild acts on, read from committed state only: an ``admitted`` receipt rebuild would
  re-admit (no committed handoff, not covered by a promotion, spec committed), or a receipt
  with a committed handoff that normal processing will still act on. Requests a promotion
  covers (A11) do not count: their only follow-up is the promotion's recovery card, so a dead
  promotion holder never triggers rebuild after rebuild. An ``admitted`` receipt rebuild must
  re-admit but cannot (its spec is not committed) also counts: rebuild settles it for good
  (below), so it triggers at most one rebuild.

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
   latest recovery request of an in-flight promotion, which is re-admitted. A request that
   must be re-admitted but has no committed spec is never skipped silently: one runtime
   ``help_request`` names it and its receipt becomes ``rejected{reason}`` (marker
   ``ledger/rebuild-problems/<Q-id>.json`` dedupes it).
4. Experiment lifecycle cards, one per experiment whose next step no open request covers (an
   open request is committed, not closed, and refs ``ar://experiment/<E>``):
   ``designed`` -> build; ``built`` -> upload; ``uploaded`` with a ``candidate_binding`` ->
   launch; ``awaiting-credits`` -> launch, unless ``lifecycle.next_launch_after`` is still in
   the future (A10 backoff); ``evaluating`` whose XP requests (``xp_recover``) all completed
   -> review; ``reviewed`` with decision ``promote`` and no promotion of it -> promote.
   Markers come from ``runtime.markers`` (shared with queue-keeper and xp-poll). Launch is
   ``launch:<E>:<launch_attempts + 1>`` (the attempt about to be made), and the launch card
   says to run ``xp recover`` before any create, so a rebuilt launch never duplicates an XP
   request. Review is ``review:<E>``. Build, upload and promote carry the epoch
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

import jsonschema
from ulid import ULID

from autoresearch.comms.publisher import OUTBOX_DIR, publish_runtime
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import load_schema
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion
from autoresearch.runtime.admission import (
    DEFAULT_BOARD_EPOCH,
    has_current_noise,
    readmit,
    receipt_path,
)
from autoresearch.runtime.handoff import handoff_path
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.markers import (
    build_marker,
    calibrate_marker,
    launch_marker,
    next_launch_attempt,
    planning_rebuild_marker,
    promote_marker,
    review_marker,
    upload_marker,
)
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
#
# Every decision reads committed state (canonical HEAD): files a crash left uncommitted on
# disk never count, so detection and rebuild always agree on what a receipt needs.

READMIT = "readmit"  # admitted, no committed handoff, not covered, spec committed
HANDOFF = "handoff"  # a committed handoff that normal processing will still act on
NO_SPEC = "no_spec"  # would be re-admitted, but its request spec is not committed
SETTLED = "settled"  # nothing for rebuild to do: closed, covered, or handoff already settled


def _committed_dir(canonical: Path, rel: str) -> list[dict]:
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{rel}/")
    found = []
    for name in sorted(listing.splitlines()):
        if name.endswith(".json"):
            record = committed_json(canonical, Path(canonical) / name)
            if record is not None:
                found.append(record)
    return found


def _receipts(canonical: Path) -> list[dict]:
    return _committed_dir(canonical, ".supervisor/ledger/requests")


def _committed_handoffs(canonical: Path, receipt: dict) -> list[dict]:
    records = []
    for task in receipt.get("tasks", []):
        directory = handoff_path(canonical, task["task_id"], "x").parent
        records += _committed_dir(canonical, directory.relative_to(canonical).as_posix())
    return records


def _committed_spec(canonical: Path, request_id: str) -> dict | None:
    request = committed_json(canonical, Layout(canonical).card_requests / f"{request_id}.json")
    return (request or {}).get("spec") or None


def _covered_by_promotion(canonical: Path, receipt: dict) -> bool:
    """A11: the request belongs to a promotion that reached submission, and is not the latest
    recovery request of the in-flight promotion (that one IS the recovery: rebuild re-admits
    it, as ``promotion._recovery_live`` expects)."""
    current = promotion.load(canonical)
    if (
        current is not None
        and current.stage in promotion.IN_FLIGHT
        and receipt["request_id"] == current.recovery_request
    ):
        return False
    return any(promotion.covers_task(canonical, t["task_id"]) for t in receipt.get("tasks", []))


def _classify(canonical: Path, receipt: dict) -> str:
    """What rebuild does with ``receipt`` (one of the constants above)."""
    if receipt["state"] not in OPEN_RECEIPT_STATES:
        return SETTLED
    handoffs = _committed_handoffs(canonical, receipt)
    if receipt["state"] == "handed_off" or handoffs:
        return HANDOFF if any(h["state"] in PROCESS_STATES for h in handoffs) else SETTLED
    if _covered_by_promotion(canonical, receipt):
        return SETTLED
    if _committed_spec(canonical, receipt["request_id"]) is None:
        return NO_SPEC
    return READMIT


def needs_rebuild(ctx: RunContext, queue: Queue) -> bool:
    """C12 detection; see the module docstring."""
    canonical = ctx.canonical
    board_exists = getattr(queue, "board_exists", None)
    if board_exists is not None and not board_exists():
        return True
    if _board_file(canonical).get("rebuilding"):
        return True
    open_tasks: set[str] = set()
    for receipt in _receipts(canonical):
        # NO_SPEC counts: rebuild settles it for good (help_request + rejected), so it
        # triggers at most one rebuild.
        if _classify(canonical, receipt) in (READMIT, HANDOFF, NO_SPEC):
            open_tasks.update(task["task_id"] for task in receipt.get("tasks", []))
    if not open_tasks:
        return False
    return not open_tasks & {card.task_id for card in queue.list()}


def problem_marker_path(canonical: Path, request_id: str) -> Path:
    return ledger_of(canonical) / "rebuild-problems" / f"{request_id}.json"


def _outbox_has(canonical: Path, text: str) -> bool:
    for path in (Path(canonical) / OUTBOX_DIR).glob("*.json"):
        message = read_json(path)
        if isinstance(message, dict) and text in str(message.get("title", "")):
            return True
    return False


def _unrecoverable(ctx: RunContext, receipt: dict, allow_no_remote: bool) -> None:
    """A request rebuild must re-admit but cannot (its spec is not committed): publish one
    runtime help_request naming it and close its receipt as ``rejected{reason}``, in one
    commit with the marker ``ledger/rebuild-problems/<Q-id>.json``. Takes the lock."""
    canonical = ctx.canonical
    request_id = receipt["request_id"]
    marker = problem_marker_path(canonical, request_id)
    reason = (
        f"board rebuild could not re-admit {request_id}: cards/requests/{request_id}.json "
        f"is not committed in canonical"
    )
    title = f"Request {request_id} lost in a board rebuild"
    with canonical_lock(canonical):
        if committed_json(canonical, marker) is not None:
            return
        if not _outbox_has(canonical, title):
            body = (
                f"After the work board was lost, request {request_id} (admitted, no handoff) "
                f"had to be re-admitted, but its spec cards/requests/{request_id}.json is not "
                f"committed, so no card can be created. Its receipt is now rejected. If the "
                f"work still matters, request it again."
            )
            publish_runtime(canonical, "help_request", title, body, [], allow_no_remote)
        path = receipt_path(ledger_of(canonical), request_id)
        rejected = receipt | {
            "state": "rejected",
            "reason": reason,
            "history": [*receipt["history"], {"state": "rejected", "at": now_iso()}],
        }
        jsonschema.validate(rejected, load_schema("receipt.json"))
        write_json(path, rejected)
        write_json(marker, {"request_id": request_id, "reason": reason, "at": now_iso()})
        commit(canonical, [path, marker], f"rebuild: {request_id} unrecoverable, rejected")
    log.warning("rebuild: %s", reason)


def _recover_requests(
    ctx: RunContext, queue: Queue, epoch: str, allow_no_remote: bool
) -> list[CardSpec]:
    """Step 3: one action per receipt (module docstring)."""
    canonical = ctx.canonical
    readmitted = []
    for receipt in _receipts(canonical):
        if receipt["state"] == "admitted" and receipt["tasks"][-1]["board_epoch"] == epoch:
            continue  # already re-admitted in this epoch (a resumed rebuild)
        kind = _classify(canonical, receipt)
        if kind == NO_SPEC:
            _unrecoverable(ctx, receipt, allow_no_remote)
        elif kind == READMIT:
            spec = CardSpec(**_committed_spec(canonical, receipt["request_id"]))
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
    attempt = next_launch_attempt(experiment)
    eid = experiment["id"]
    why = (
        f"Launch the XP evaluation of {eid}, attempt {attempt} (the board was rebuilt). First "
        f'run "$AUTORESEARCH_CLI" softmax xp recover {eid}: requests it finds already exist '
        f'and must not be created again. Then run "$AUTORESEARCH_CLI" softmax xp create {eid} '
        f"--batch <n> --attempt {attempt}, which creates only the requests still missing."
    )
    return launch_marker(eid, attempt), f"Launch {eid}", why, "experimentation"


def _lifecycle_step(
    canonical: Path, experiment: dict, sm: Softmax, epoch: str, now: datetime.datetime
) -> tuple[str, str, str, str] | None:
    """(marker, title, why, mode) of the card that moves ``experiment`` on, or None."""
    eid, status = experiment["id"], experiment.get("status")
    if status == "designed":
        why = f"Experiment {eid} is designed but not built (board rebuilt). Build the candidate."
        return build_marker(eid, epoch), f"Build {eid}", why, "experimentation"
    if status == "built":
        why = f"Experiment {eid} is built but not uploaded (board rebuilt). Upload the policy."
        return upload_marker(eid, epoch), f"Upload {eid}", why, "experimentation"
    if status == "uploaded" and experiment.get("candidate_binding"):
        return _launch(experiment)
    if status == "awaiting-credits":
        after = _parse_time((experiment.get("lifecycle") or {}).get("next_launch_after"))
        return None if after is not None and now < after else _launch(experiment)
    if status == "evaluating" and _xp_completed(sm, eid):
        why = f"The XP requests of {eid} completed (board rebuilt). Review the results."
        return review_marker(eid), f"Review {eid}", why, "experimentation"
    decision = experiment.get("decision") or {}
    if (
        status == "reviewed"
        and decision.get("outcome") == "promote"
        and not _promoted(canonical, eid)
    ):
        why = f"Experiment {eid} was reviewed with decision promote and has no promotion yet."
        return promote_marker(eid, epoch), f"Promote {eid}", why, "maintenance"
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
        new = commit_runtime_request(canonical, calibrate_marker(game_version), spec)
    return [new] if new is not None else []


def rebuild(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    *,
    game_version: str,
    now: datetime.datetime | None = None,
    allow_no_remote: bool = False,
) -> list[CardSpec]:
    """C12 rebuild (module docstring). ``game_version`` is the league's current game version
    (calibration); ``now`` (default: the current time) is for the A10 launch backoff;
    ``allow_no_remote`` is for the help_request of an unrecoverable request (tests).
    Returns the specs of the cards it created, re-admitted requests included. The caller
    must not hold the canonical lock."""
    canonical = ctx.canonical
    now = now or datetime.datetime.now(datetime.UTC)
    with canonical_lock(canonical):
        epoch = _start_epoch(canonical)
    ensure_board = getattr(queue, "ensure_board", None)
    if ensure_board is not None:
        ensure_board()
    created = _recover_requests(ctx, queue, epoch, allow_no_remote)
    created += _lifecycle_cards(ctx, sm, epoch, now)
    created += _reconcile_promotion(ctx, queue, sm)
    created += _calibration(ctx, game_version)
    planning = request_planning(
        ctx, queue, planning_rebuild_marker(epoch), f"The board was rebuilt (epoch {epoch})."
    )
    if planning is not None:
        created.append(planning)
    with canonical_lock(canonical):
        _finish_epoch(canonical)
    return created
