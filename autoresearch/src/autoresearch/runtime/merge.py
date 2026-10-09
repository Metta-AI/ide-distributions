"""Handoff processing and merge: ``queue-keeper`` as the single canonical writer (Overview C3,
C4, C5, C12).

``card finish`` leaves a committed handoff record per card run
(``.supervisor/ledger/handoffs/<task>/<run>.json``, state ``committed``). ``process_handoffs``
advances each record whose run is terminal (or whose task belongs to an earlier board epoch):

    committed --contains(result)--> merged
    committed --validate_at ok--> validated      (invalid --> repair)
    validated --contains(result)--> merged
    validated --W∩M empty, merge ok--> merged    (overlap or textual conflict --> reconcile)
    merged --publish(...)--> published           (request receipt --> published{merge_sha})

A ``repair``/``reconcile`` handoff gets one runtime card request (P0, refs = the original refs
plus ``ar://handoff/<task>/<run>``) written into canonical ``cards/requests/`` and committed with
the state. When that follow-up card's own handoff publishes, every repair/reconcile handoff it
references becomes ``superseded{by}`` (and its request receipt too) and is never processed again.

Crash safety. The handoff state at canonical HEAD is the truth; each transition writes its files
and commits them in one small commit. A crash before a commit just repeats the step: validation
is pure, a merge is skipped when canonical already contains the result (no second merge commit,
no revalidation), a follow-up request left uncommitted is found and reused, and ``publish`` is
called again. ``publish`` must therefore be idempotent.

Foreign cards. A handoff whose request has no committed create request (admission never
created its card; its receipt was forged, ``runtime.foreign``) is never merged, in any board
epoch: the card gets one marker and one runtime help_request, and the other handoffs go on.
The check reads the ledger only, before any board read.

All canonical mutations run under ``canonical.lock``, which is not re-entrant: callers must
not hold it, and ``publish`` is called while it is held and must not take it.

Promotion hooks (C8, ``promotion.py``), all under the lock: ``promotion.merge_gate`` runs
before a merge and can make the handoff wait (its promotion is still at ``intent``), mark
it ``repair`` owned by the promotion (holder branch lacks the candidate champion; no
follow-up card, the promotion-recovery card is the only follow-up), or send it to
``reconcile`` (another branch changes the champion); ``promotion.after_merge`` runs once a
handoff is ``merged`` (stage ``recorded``); and ``promotion.after_publish`` runs right after
``publish`` (stage ``published``). Each is idempotent, so a crash before the handoff's next
commit just repeats it. The orphan scan skips tasks an in-flight promotion covers.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.kanban_queue import ForeignCardError
from autoresearch.queue.requests import load_schema, write_request
from autoresearch.records.registry import parse_record_path
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion, provenance
from autoresearch.runtime.admission import admitted_spec, board_epoch, receipt_path
from autoresearch.runtime.foreign import is_foreign, record_foreign
from autoresearch.runtime.handoff import (
    HANDOFFS_DIR,
    card_spec,
    handoff_path,
    validate_at,
    write_handoff,
)
from autoresearch.runtime.ledger import (
    canonical_lock,
    canonical_of,
    commit,
    committed_json,
    ledger_of,
    now_iso,
)

HANDOFF_REF_PREFIX = "ar://handoff/"
MISSING = "missing"  # fragment of the handoff ref for a completed run that left no handoff
PROCESS_STATES = ("committed", "validated", "merged")
FOLLOW_UP_STATES = ("repair", "reconcile")
OPEN_RECEIPT_STATES = ("admitted", "handed_off")
RUNTIME = "runtime"

# publish(canonical, merge_sha, handoff record): called after a merge, under the canonical lock.
# The record it gets also has ``run_summary`` (never stored): the summary the card's run ended
# with on the board (``RunInfo.summary``, the ``kanban_complete`` summary), or None.
Publish = Callable[[Path, str, dict], None]
RUN_SUMMARY = "run_summary"


@dataclass
class HandoffOutcome:
    task_id: str
    run_id: str
    state: str  # the handoff state this call left it in
    merge_sha: str | None = None
    follow_up_request: str | None = None


def handoff_ref(task_id: str, run_id: str) -> str:
    return f"{HANDOFF_REF_PREFIX}{task_id}/{run_id}"


def orphan_marker_path(ledger: Path, task_id: str, run_id: str) -> Path:
    """Marker for a completed run with no handoff; it dedupes the repair request and is what
    ``ar://handoff/<task>/<run>#missing`` resolves to."""
    return ledger / "orphans" / f"{task_id}-{run_id}.json"


def _no_publish(canonical: Path, merge_sha: str, handoff: dict) -> None:
    del canonical, merge_sha, handoff


# --- small state helpers ---


def _with_state(document: dict, state: str, **fields) -> dict:
    history = [*document["history"], {"state": state, "at": now_iso()}]
    return document | fields | {"state": state, "history": history}


def _write_receipt(path: Path, receipt: dict, state: str, **fields) -> None:
    moved = _with_state(receipt, state, **fields)
    jsonschema.validate(moved, load_schema("receipt.json"))
    write_json(path, moved)


def _label(record: dict) -> str:
    return f"{record['card']}/{record['run_id']}"


def _transition(
    canonical: Path, path: Path, record: dict, state: str, extra: tuple[Path, ...] = (), **fields
) -> dict:
    """Write the handoff in ``state`` and commit it with ``extra`` paths in one commit."""
    record = _with_state(record, state, **fields)
    write_handoff(path, record)
    commit(canonical, [path, *extra], f"handoff {_label(record)}: {state}")
    return record


# --- readiness ---


def _committed_handoffs(canonical: Path) -> list[tuple[Path, dict]]:
    """Handoff records at canonical HEAD, oldest first."""
    listing = gitops.git(canonical, "ls-tree", "-r", "--name-only", "HEAD", f"{HANDOFFS_DIR}/")
    found = []
    for rel in listing.splitlines():
        if rel.endswith(".json"):
            path = canonical / rel
            found.append((path, committed_json(canonical, path)))
    found.sort(key=lambda item: (item[1]["history"][0]["at"], item[0].as_posix()))
    return found


def _task_epoch(receipt: dict | None, task_id: str) -> str | None:
    for task in (receipt or {}).get("tasks", []):
        if task["task_id"] == task_id:
            return task["board_epoch"]
    return None


def _terminal_run(queue: Queue, canonical: Path, record: dict) -> tuple[bool, str | None]:
    """(terminal, run summary): terminal if the run has ended, or its task belongs to an
    earlier board epoch (adopted with no run lookup and no summary, C3/C12). An unknown task in
    the current epoch keeps waiting."""
    ledger = ledger_of(canonical)
    receipt = committed_json(canonical, receipt_path(ledger, record["request_id"]))
    epoch = _task_epoch(receipt, record["card"])
    if epoch is not None and epoch != board_epoch(ledger):
        return True, None
    try:
        card = queue.get(record["card"])
    except LookupError:
        return False, None
    run_id = str(record["run_id"])
    for run in card.runs:
        if str(run.run_id) == run_id and run.terminal:
            return True, run.summary
    return False, None


# --- follow-up requests ---


def _pending_follow_up(canonical: Path, ref: str) -> str | None:
    """A runtime request already written for ``ref`` (its last ref), e.g. by a step that
    crashed before its commit."""
    for path in sorted(Layout(canonical).card_requests.glob("Q-*.json")):
        spec = read_json(path).get("spec") or {}
        if spec.get("created_by") == RUNTIME and spec.get("refs", [])[-1:] == [ref]:
            return path.stem
    return None


def _write_follow_up(canonical: Path, title: str, mode: str, refs: list[str], why: str) -> Path:
    """Write (or find the uncommitted copy of) the runtime card request whose last ref is
    ``refs[-1]``; return its path. The caller commits it."""
    request_id = _pending_follow_up(canonical, refs[-1])
    if request_id is None:
        spec = CardSpec(
            request_id="",  # write_request assigns the id
            title=title,
            mode=mode,
            why=why,
            refs=refs,
            created_by=RUNTIME,
            priority="P0",
        )
        request_id = write_request(Layout(canonical), "create", spec=spec)
    return Layout(canonical).card_requests / f"{request_id}.json"


def _follow_up_refs(refs: list[str], ref: str) -> list[str]:
    return [*[existing for existing in refs if existing != ref], ref]


def _request_follow_up(
    canonical: Path, path: Path, record: dict, kind: str, why: str, **fields
) -> dict:
    ref = handoff_ref(record["card"], record["run_id"])
    title = f"{kind.capitalize()} handoff {_label(record)}"
    request = _write_follow_up(
        canonical, title, record["mode"], _follow_up_refs(record["refs"], ref), why
    )
    return _transition(
        canonical, path, record, kind, (request,), follow_up_request=request.stem, **fields
    )


# --- steps ---


def _reconcile_receipt(canonical: Path, record: dict) -> None:
    """Move the request receipt to ``handed_off`` if a crash left it ``admitted``."""
    path = receipt_path(ledger_of(canonical), record["request_id"])
    receipt = committed_json(canonical, path)
    if receipt is None or receipt["state"] != "admitted":
        return
    handed_off = {"task_id": record["card"], "run_id": record["run_id"]}
    _write_receipt(path, receipt, "handed_off", handed_off=handed_off)
    commit(canonical, [path], f"handoff {_label(record)}: receipt handed_off")


def _is_ancestor(canonical: Path, ancestor: str, sha: str) -> bool:
    try:
        gitops.git(canonical, "merge-base", "--is-ancestor", ancestor, sha)
    except gitops.GitError:
        return False
    return True


def _merge_commit_of(canonical: Path, result_sha: str) -> str:
    """The commit that brought ``result_sha`` into canonical's mainline: the first merge after
    it whose first parent did not contain it; ``result_sha`` itself if it arrived otherwise."""
    merges = gitops.git(
        canonical,
        "rev-list",
        "--merges",
        "--ancestry-path",
        "--topo-order",
        "--reverse",
        f"{result_sha}..HEAD",
    ).split()
    for sha in merges:
        if not _is_ancestor(canonical, result_sha, f"{sha}^1"):
            return sha
    return result_sha


def _mark_merged(canonical: Path, path: Path, record: dict, merge_sha: str) -> dict:
    return _transition(canonical, path, record, "merged", merge_sha=merge_sha)


def _validate(canonical: Path, path: Path, record: dict) -> dict:
    report = validate_at(
        canonical,
        record["base_sha"],
        record["result_sha"],
        card_spec(record),
        list(record["responsible_inbound"]),
        record["card"],
        outcome=record.get("outcome"),
    )
    record = record | {"report": report.to_dict()}
    if report.ok:
        return _transition(canonical, path, record | {"journal": report.journal}, "validated")
    why = (
        f"Handoff {_label(record)} of request {record['request_id']} failed validation at "
        f"{record['result_sha']} (base {record['base_sha']}): " + "; ".join(report.problems)
    )
    return _request_follow_up(canonical, path, record, "repair", why)


def _changed_ids(canonical: Path, store: RecordStore, since: str) -> set[str]:
    """Record ids changed in canonical from ``since`` to HEAD (deletions and archive moves
    count as changes of the id)."""
    ids = set()
    for _, rel in gitops.diff_status(canonical, since, "HEAD"):
        parsed = parse_record_path(store.layout, rel)
        if parsed is not None:
            ids.add(parsed[1])
    return ids


def _reconcile_why(record: dict, head: str, overlap: list[str], conflicts: list[str]) -> str:
    why = (
        f"Handoff {_label(record)} of request {record['request_id']} cannot merge cleanly: "
        f"result {record['result_sha']} (base {record['base_sha']}) against canonical {head}."
    )
    if overlap:
        why += " Entities changed on both sides: " + ", ".join(overlap) + "."
    if conflicts:
        why += " Conflicting files: " + ", ".join(conflicts) + "."
    return why


def _merge(canonical: Path, path: Path, record: dict, store: RecordStore) -> dict:
    """C4: reconcile if the card changed an existing entity canonical also changed since the
    card's base (W∩M), even when git would merge; else merge, reconciling on a conflict.

    M is measured from merge-base(result, HEAD), which is the recorded base unless an earlier
    run of the same branch already merged, so this result's own merged commits are excluded."""
    head = gitops.head(canonical)
    since = gitops.merge_base(canonical, record["result_sha"], head)
    written = set(record["report"]["changed_existing"])
    overlap = sorted(written & _changed_ids(canonical, store, since))
    if overlap:
        why = _reconcile_why(record, head, overlap, [])
        return _request_follow_up(
            canonical, path, record, "reconcile", why, overlap=overlap, conflicts=[]
        )
    message = f"merge card/{record['request_id']} ({_label(record)})"
    conflicts: list[str] = []

    def merge_commit() -> gitops.PreparedCommit | None:
        prepared, found = gitops.prepare_merge(canonical, record["result_sha"], message)
        conflicts.extend(found)
        return prepared

    merge_sha = provenance.commit_recorded(canonical, message, merge_commit)
    if merge_sha is None:
        why = _reconcile_why(record, head, [], conflicts)
        return _request_follow_up(
            canonical, path, record, "reconcile", why, overlap=[], conflicts=conflicts
        )
    return _mark_merged(canonical, path, record, merge_sha)


def _supersede(canonical: Path, ref: str, by: str) -> list[Path]:
    """Mark the repair/reconcile handoff (or missing-handoff marker) named by ``ref`` and its
    request receipt superseded by ``by``; return the paths written."""
    task_id, _, run_id = ref.removeprefix(HANDOFF_REF_PREFIX).split("#", 1)[0].partition("/")
    ledger = ledger_of(canonical)
    original_path = handoff_path(canonical, task_id, run_id)
    original = committed_json(canonical, original_path)
    if original is not None:
        if original["state"] not in FOLLOW_UP_STATES:
            return []
        write_handoff(original_path, _with_state(original, "superseded", superseded_by=by))
        written, request_id = [original_path], original["request_id"]
    else:
        marker_path = orphan_marker_path(ledger, task_id, run_id)
        marker = committed_json(canonical, marker_path)
        if marker is None or "superseded_by" in marker:
            return []
        write_json(marker_path, marker | {"superseded_by": by})
        written, request_id = [marker_path], marker["request_id"]
    receipt_file = receipt_path(ledger, request_id)
    receipt = committed_json(canonical, receipt_file)
    if receipt is not None and receipt["state"] in OPEN_RECEIPT_STATES:
        _write_receipt(receipt_file, receipt, "superseded", superseded_by=by)
        written.append(receipt_file)
    return written


def _publish(
    canonical: Path, path: Path, record: dict, publish: Publish, summary: str | None
) -> dict:
    """Run ``publish``, then in one commit: handoff ``published``, its receipt
    ``published{merge_sha}``, and every repair/reconcile handoff it references superseded."""
    publish(canonical, record["merge_sha"], record | {RUN_SUMMARY: summary})
    by = _label(record)
    written = []
    for ref in record["refs"]:
        if ref.startswith(HANDOFF_REF_PREFIX):
            written += _supersede(canonical, ref, by)
    receipt_file = receipt_path(ledger_of(canonical), record["request_id"])
    receipt = committed_json(canonical, receipt_file)
    if receipt is not None and receipt["state"] in OPEN_RECEIPT_STATES:
        _write_receipt(receipt_file, receipt, "published", merge_sha=record["merge_sha"])
        written.append(receipt_file)
    return _transition(canonical, path, record, "published", tuple(written))


def _publish_then_promote(ctx: RunContext, publish: Publish) -> Publish:
    """``publish``, then the promotion publish hook, before the handoff's ``published`` commit."""

    def run(canonical: Path, merge_sha: str, record: dict) -> None:
        publish(canonical, merge_sha, record)
        promotion.after_publish(ctx, record)

    return run


def _advance(
    ctx: RunContext, path: Path, store: RecordStore, publish: Publish, summary: str | None
) -> dict:
    """Drive one handoff as far as it goes; call under the canonical lock."""
    canonical = ctx.canonical
    record = committed_json(canonical, path)
    if record["state"] not in PROCESS_STATES:
        return record
    _reconcile_receipt(canonical, record)
    unmerged = record["state"] in ("committed", "validated")
    if unmerged and gitops.contains(canonical, record["result_sha"]):
        record = _mark_merged(
            canonical, path, record, _merge_commit_of(canonical, record["result_sha"])
        )
    if record["state"] == "committed":
        record = _validate(canonical, path, record)
    if record["state"] == "validated":
        gate = promotion.merge_gate(canonical, record)
        if gate is None:
            record = _merge(canonical, path, record, store)
        elif gate.kind == promotion.REPAIR:
            record = _transition(canonical, path, record, "repair", promotion=gate.promotion_id)
        elif gate.kind == promotion.RECONCILE:
            record = _request_follow_up(canonical, path, record, "reconcile", gate.why)
        # promotion.WAIT: stays validated until reconcile settles the promotion's intent
    if record["state"] == "merged":
        promotion.after_merge(ctx, record)
        record = _publish(canonical, path, record, _publish_then_promote(ctx, publish), summary)
    return record


def _board_title(queue: Queue, task_id: str) -> str | None:
    """The card's title on the board, for the foreign-card message; None if unreadable."""
    try:
        return queue.get(task_id).spec.title
    except ForeignCardError as error:
        return error.title
    except Exception:  # noqa: BLE001 - the title is only a courtesy for the message
        return None


def _ready(
    ctx: RunContext, queue: Queue, record: dict, allow_no_remote: bool
) -> tuple[bool, str | None]:
    """``(ready, run summary)``: whether ``process_handoffs`` should advance this handoff now.
    A foreign handoff is recorded (``runtime.foreign``) and is never ready."""
    canonical = ctx.canonical
    if admitted_spec(canonical, record["request_id"]) is None:
        title = _board_title(queue, record["card"])
        record_foreign(
            canonical, record["card"], title, ctx.hermes_home.name, allow_no_remote,
            forged_handoff=True,
        )  # fmt: skip
        return False, None
    return _terminal_run(queue, canonical, record)


def _left_unchanged(canonical: Path, head_before: str) -> bool:
    """Canonical is as it was before a failed ``_advance``: HEAD has not moved, no merge is in
    progress and nothing is uncommitted (a half-done merge or publication shows as one of
    these)."""
    if gitops.head(canonical) != head_before:
        return False
    try:
        gitops.git(canonical, "rev-parse", "-q", "--verify", "MERGE_HEAD")
        return False
    except gitops.GitError:
        return gitops.git(canonical, "status", "--porcelain") == ""


def process_handoffs(
    ctx: RunContext,
    queue: Queue,
    store: RecordStore,
    publish: Publish | None = None,
    allow_no_remote: bool = False,
) -> list[HandoffOutcome]:
    """Advance every committed/validated/merged handoff whose run is terminal (C3).

    ``store`` is canonical's record store (its layout maps paths to entity ids). ``publish``
    defaults to a no-op; see the module docstring for its contract. The caller must not hold
    the canonical lock. Returns one outcome per handoff processed.

    Foreign handoffs. Before any board read, a handoff whose request has no committed create
    request (``admission.admitted_spec``) is foreign: admission never created its card, and
    its receipt was forged. It is never merged, in any board epoch: the card is recorded
    once (``runtime.foreign``: marker and help_request; ``allow_no_remote`` is for that
    message) and skipped from then on. The board is not trusted for this: a forged receipt
    can name an earlier board epoch, which skips the board lookup.

    One card never stops the others: any error while deciding whether a handoff is ready
    (the board cannot show its card, an admitted card's body is unreadable, recording a
    foreign card fails) leaves that handoff for the next run, and once every handoff was
    tried the errors are raised together, so the step is logged as failed. The outcomes of
    such a run are lost to the caller (only its log counts); the handoffs are committed. An
    error while advancing a handoff (``_advance``: validate, merge, publish) is treated the
    same way only if canonical is left unchanged (``_left_unchanged``); otherwise it stops the
    step at once, because canonical may be mid-transaction (a merge or a partial publication)
    and the next run repairs that first (module docstring, crash safety)."""
    canonical = ctx.canonical
    publish = publish or _no_publish
    outcomes = []
    failures: list[str] = []
    journal = None
    for path, record in _committed_handoffs(canonical):
        if record["state"] not in PROCESS_STATES or is_foreign(canonical, record["card"]):
            continue
        try:
            ready, summary = _ready(ctx, queue, record, allow_no_remote)
        except Exception as error:  # noqa: BLE001 - per-card containment, raised below
            failures.append(f"{_label(record)}: {type(error).__name__}: {error}")
            journal = journal or provenance.journal_error(error)
            continue
        if not ready:
            continue
        head_before = gitops.head(canonical)
        try:
            with canonical_lock(canonical):
                record = _advance(ctx, path, store, publish, summary)
        except Exception as error:
            if not _left_unchanged(canonical, head_before):
                raise
            failures.append(f"{_label(record)}: {type(error).__name__}: {error}")
            journal = journal or provenance.journal_error(error)
            continue
        outcomes.append(
            HandoffOutcome(
                task_id=record["card"],
                run_id=record["run_id"],
                state=record["state"],
                merge_sha=record.get("merge_sha"),
                follow_up_request=record.get("follow_up_request"),
            )
        )
    message = "handoff(s) not processed: " + "; ".join(failures)
    if journal is not None:  # typed, so queue-keeper fails closed
        raise provenance.JournalError(message) from journal
    if failures:
        raise RuntimeError(message)
    return outcomes


# --- completed runs with no handoff ---


def _orphan_repair(canonical: Path, task_id: str, run_id: str, spec: CardSpec) -> str | None:
    """Write and commit the repair request and marker for one orphaned run; call under the
    lock. None if already handled or the request is closed."""
    ledger = ledger_of(canonical)
    marker_path = orphan_marker_path(ledger, task_id, run_id)
    if committed_json(canonical, handoff_path(canonical, task_id, run_id)) is not None:
        return None
    if committed_json(canonical, marker_path) is not None:
        return None
    receipt = committed_json(canonical, receipt_path(ledger, spec.request_id))
    if receipt is not None and receipt["state"] not in OPEN_RECEIPT_STATES:
        return None
    ref = f"{handoff_ref(task_id, run_id)}#{MISSING}"
    why = (
        f"Card {task_id} of request {spec.request_id} completed run {run_id} without a handoff "
        f"record (card finish did not run); redo or recover its work from branch "
        f"card/{spec.request_id} if it exists."
    )
    request = _write_follow_up(
        canonical,
        f"Repair missing handoff {task_id}/{run_id}",
        spec.mode,
        _follow_up_refs(spec.refs, ref),
        why,
    )
    marker = {
        "task_id": task_id,
        "run_id": run_id,
        "request_id": spec.request_id,
        "follow_up_request": request.stem,
        "at": now_iso(),
    }
    write_json(marker_path, marker)
    commit(canonical, [marker_path, request], f"handoff {task_id}/{run_id}: {MISSING}, repair")
    return request.stem


def find_orphan_done(queue: Queue, ledger: Path) -> list[str]:
    """For each completed card whose last run ended (not dropped) with no handoff record for
    that run, write one repair request (refs = the card's refs plus
    ``ar://handoff/<task>/<run>#missing``). Deduplicated by ``orphan_marker_path``. Returns
    the request ids written. The caller must not hold the canonical lock."""
    canonical = canonical_of(ledger)
    written = []
    for card in queue.list("completed"):
        if not card.runs:
            continue
        run = card.runs[-1]
        if not run.terminal or run.outcome == "dropped":
            continue
        if promotion.covers_task(canonical, card.task_id):
            continue  # promotion reconcile owns its holder and recovery cards
        with canonical_lock(canonical):
            request_id = _orphan_repair(canonical, card.task_id, str(run.run_id), card.spec)
        if request_id is not None:
            written.append(request_id)
    return written
