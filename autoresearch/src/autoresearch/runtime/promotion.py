"""Promotion: submit a candidate policy to the live league, crash-safely (Overview C8).

Durable state is committed in canonical: ``.supervisor/ledger/promotion.json`` holds the
current promotion and ``.supervisor/ledger/promotions/<P-id>.json`` keeps one file per
promotion (C4), both rewritten in the same commit on every transition. Stages:

    acquired --record_intent--> intent --record_submitted--> submitted
    submitted --on_merge (champion in the merge == candidate)--> recorded
    recorded --on_published--> published
    acquired --holder run terminal, taken over by a new acquire--> aborted
    intent --reconcile: holder terminal, no submission on Softmax--> aborted
    intent --reconcile: holder terminal, submission found--> submitted
    intent --ledger push failed (record_intent)--> acquired

Rules that make a crash at any point safe:

- Only one promotion is in flight. ``acquire`` takes over only a ``published`` or
  ``aborted`` promotion, or one abandoned in ``acquired`` (its holder run is terminal, or
  its board epoch is earlier). A promotion in ``intent``, ``submitted`` or ``recorded`` is
  advanced only by the runtime (``reconcile``, merge hooks) or by its recovery card.
- Nothing is submitted before ``intent`` is committed and pushed together with the
  candidate branch, so a lost host can always find out what may have been submitted.
- ``submit_promotion`` is the single submit path. It submits only from ``acquired`` via
  ``intent``; a promotion at ``intent`` or later is never submitted again. ``reconcile``
  settles a crashed ``intent`` from the Softmax submissions list, never by resubmitting.
Liveness (one rule, in ``reconcile``): a ``submitted`` promotion needs recovery unless a
live holder-role run exists. The holder role belongs to the holder TASK (a retried run after
a rate limit or reclaim is accepted; ``on_merge`` rebinds ``run_id``). It is live while the
task has a handoff that normal processing will act on (``committed|validated|merged``), or
is pending/claimed in the current board epoch, or the latest recovery card is still live.
A holder handoff in ``repair``, ``reconcile``, ``superseded`` or ``published`` without
recording all count as "needs recovery". Recovery is a P0 maintenance card with key
``promote-recover:<P-id>:<attempt>``; a new attempt is requested only when the previous one
is closed or dead, so at most one is live. Its worker runs ``promotion adopt`` (only the
latest attempt may), ``promotion stage-champion`` and ``card finish``; its merge drives
``recorded`` and ``published`` without resubmitting. After ``MAX_RECOVERY_ATTEMPTS`` dead
attempts, one runtime ``help_request`` is published and no more cards are created for that
promotion until an operator acts. The orphan scan skips holder, former holder and recovery
tasks of every promotion that reached submission, so recovery is the only follow-up.
Publishing closes what the promotion covered in the same commit: its promotion-owned
``repair`` handoffs become ``superseded``, and open receipts of its dead holder and recovery
requests become ``superseded{by: P-id}``, so a C12 rebuild re-admits none of them.
Promotions aborted before submitting cover nothing: redoing their card is a fair retry.

Merge integration (``merge.py`` calls these under the canonical lock): ``merge_gate`` before
a merge — a holder-task handoff WAITs while the promotion is at ``intent`` (never merges
there), merges at ``submitted`` when its champion is the candidate, else is marked
``repair`` owned by the promotion (``promotion: <P-id>``, no follow-up card); any other
branch that changes the champion path is a ``reconcile`` ("champion mismatch").
``after_merge`` (stage ``recorded``) and ``after_publish`` (stage ``published``).

Locking. ``acquire``, ``record_intent``, ``record_submitted``, ``adopt``, ``reconcile`` and
``submit_promotion`` take ``canonical.lock`` (callers must not hold it); ``on_merge``,
``on_published`` and the merge hooks run while the caller holds it. Network calls (git push,
Softmax) run outside the lock.

The champion is the candidate STRATEGY at ``CHAMPION_PATH``. A ``candidate_ref`` is stored as
``<branch>:<path>`` (git's ``rev:path``): the holder's card branch and the candidate STRATEGY
path in it, normally ``strategy/candidates/<E-id>/STRATEGY.md``.
"""

import logging
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import jsonschema
from ulid import ULID

from autoresearch.comms.publisher import outbox_has_title, publish_runtime
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import load_schema, write_request
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.admission import board_epoch, receipt_path
from autoresearch.runtime.handoff import handoff_path, write_handoff
from autoresearch.runtime.ledger import (
    canonical_lock,
    commit,
    committed_json,
    ledger_of,
    now_iso,
)
from autoresearch.softmax_tool.protocol import Softmax

CHAMPION_PATH = "strategy/STRATEGY.md"
PROMOTION_FILE_REL = ".supervisor/ledger/promotion.json"
HISTORY_DIR_REL = ".supervisor/ledger/promotions"
REMOTE = "origin"  # canonical's remote (AUTORESEARCH_WORKSPACE_REMOTE, set up by bootstrap)
MAIN_REFSPEC = "HEAD:refs/heads/main"
IN_FLIGHT = ("intent", "submitted", "recorded")
RUNTIME = "runtime"
MAX_RECOVERY_ATTEMPTS = 3

log = logging.getLogger(__name__)


class PromotionError(RuntimeError):
    """A promotion step was refused; the durable state is unchanged (see the message)."""


@dataclass
class Promotion:
    """The C8 ledger record, plus the submission id, merge sha, recovery requests and history."""

    id: str
    holder_task: str
    run_id: str
    board_epoch: str
    experiment: str
    version: str
    platform_policy_version_id: str
    candidate_ref: str
    candidate_sha: str | None
    stage: str
    updated_at: str
    submission_id: str | None = None
    merge_sha: str | None = None
    recovery_requests: list[str] = field(default_factory=list)  # one per attempt, in order
    former_holders: list[str] = field(default_factory=list)  # holder tasks before adoptions
    help_requested: bool = False  # recovery attempts exhausted; an operator must act
    history: list[dict] = field(default_factory=list)

    @property
    def recovery_request(self) -> str | None:
        """The latest recovery attempt's request id (the only one that may adopt)."""
        return self.recovery_requests[-1] if self.recovery_requests else None

    @property
    def candidate_branch(self) -> str:
        return self.candidate_ref.partition(":")[0]

    @property
    def candidate_path(self) -> str:
        return self.candidate_ref.partition(":")[2]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Promotion":
        return cls(**data)


@dataclass
class Action:
    """One thing ``reconcile`` did: ``submitted``, ``aborted``, ``recovery_requested``,
    ``help_requested`` or ``published``."""

    kind: str
    promotion_id: str
    detail: str = ""
    request_id: str | None = None


@dataclass
class SubmitOutcome:
    ok: bool
    promotion_id: str | None = None
    submission_id: str | None = None
    reason: str = ""
    request_id: str | None = None  # the re-evaluation request written on a champion mismatch


# --- storage ---


def promotion_path(canonical: Path) -> Path:
    return Path(canonical) / PROMOTION_FILE_REL


def history_path(canonical: Path, promotion_id: str) -> Path:
    return Path(canonical) / HISTORY_DIR_REL / f"{promotion_id}.json"


def load(canonical: Path) -> Promotion | None:
    """The current promotion as committed at canonical HEAD (uncommitted writes do not count)."""
    data = committed_json(canonical, promotion_path(canonical))
    return None if data is None else Promotion.from_dict(data)


def _save(canonical: Path, promotion: Promotion, message: str, extra: tuple[Path, ...] = ()):
    """Write the current and history files and commit them with ``extra``; under the lock."""
    paths = [promotion_path(canonical), history_path(canonical, promotion.id)]
    for path in paths:
        write_json(path, promotion.to_dict())
    commit(canonical, [*paths, *extra], message)


def _move(
    canonical: Path,
    promotion: Promotion,
    stage: str,
    note: str = "",
    extra: tuple[Path, ...] = (),
    **fields,
) -> Promotion:
    """Move to ``stage`` (with ``fields``), append history, commit; under the lock."""
    at = now_iso()
    entry = {"stage": stage, "at": at} | ({"note": note} if note else {})
    moved = replace(
        promotion, stage=stage, updated_at=at, history=[*promotion.history, entry], **fields
    )
    _save(canonical, moved, f"promotion {promotion.id}: {stage}", extra)
    return moved


def _require(current: Promotion | None, promotion_id: str, *stages: str) -> Promotion:
    if current is None or current.id != promotion_id:
        raise PromotionError(f"promotion {promotion_id} is not the current promotion")
    if current.stage not in stages:
        raise PromotionError(
            f"promotion {promotion_id} is {current.stage}; this step needs {' or '.join(stages)}"
        )
    return current


def _all_promotions(canonical: Path) -> list[Promotion]:
    """Every promotion record at canonical HEAD, oldest first (ids are ULIDs and promotions
    are serialized, so id order is creation and publication order)."""
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{HISTORY_DIR_REL}/")
    found = []
    for rel in sorted(listing.splitlines()):
        data = committed_json(canonical, Path(canonical) / rel)
        if data is not None:
            found.append(Promotion.from_dict(data))
    return found


def _published(canonical: Path) -> list[Promotion]:
    return [p for p in _all_promotions(canonical) if p.stage == "published"]


# --- holder and worker identity ---


def _worker_identity(ctx: RunContext) -> tuple[str, str]:
    if not ctx.card_id or not ctx.run_id:
        raise PromotionError("promotion steps need a card worker context (task id and run id)")
    return ctx.card_id, str(ctx.run_id)


def _is_holder(promotion: Promotion, task_id: str, run_id) -> bool:
    return promotion.holder_task == task_id and promotion.run_id == str(run_id)


def _holder_terminal(canonical: Path, queue: Queue, promotion: Promotion) -> bool:
    """True if the holder run has ended, or the promotion is from an earlier board epoch (the
    board that held the run is gone, C12). An unknown task in the current epoch is live."""
    if promotion.board_epoch != board_epoch(ledger_of(canonical)):
        return True
    try:
        card = queue.get(promotion.holder_task)
    except LookupError:
        return False
    return any(str(run.run_id) == promotion.run_id and run.terminal for run in card.runs)


# --- experiment facts ---


def _experiment(canonical: Path, experiment: str) -> dict:
    meta, _ = RecordStore(Layout(canonical)).load(experiment)
    return meta


def _platform_id(canonical: Path, experiment: str, version: str) -> str:
    binding = _experiment(canonical, experiment).get("candidate_binding") or {}
    if binding.get("version_id") != version:
        raise PromotionError(
            f"{experiment} is not bound to {version} (candidate_binding: {binding or 'none'})"
        )
    return binding["platform_policy_version_id"]


def _baseline(canonical: Path, experiment: str) -> str:
    return (_experiment(canonical, experiment).get("evaluation_design") or {}).get(
        "baseline_version", ""
    )


# --- runtime card requests ---


def _uncommitted_request_with(canonical: Path, key: str) -> str | None:
    """A runtime request already written with ``key`` in its title (by a run that crashed
    before its commit, or an earlier refusal)."""
    for path in sorted(Layout(canonical).card_requests.glob("Q-*.json")):
        spec = read_json(path).get("spec") or {}
        if spec.get("created_by") == RUNTIME and key in spec.get("title", ""):
            return path.stem
    return None


def _write_runtime_request(
    canonical: Path, key: str, title: str, mode: str, refs: list[str], why: str, priority: str
) -> Path:
    """Write (or find) the runtime card request carrying ``key``; the caller commits it."""
    request_id = _uncommitted_request_with(canonical, key)
    if request_id is None:
        spec = CardSpec(
            request_id="",  # write_request assigns the id
            title=f"{title} ({key})",
            mode=mode,
            why=why,
            refs=refs,
            created_by=RUNTIME,
            priority=priority,
        )
        request_id = write_request(Layout(canonical), "create", spec=spec)
    return Layout(canonical).card_requests / f"{request_id}.json"


def _refs(experiment: str, version: str) -> list[str]:
    return [f"ar://experiment/{experiment}", f"ar://version/{version}"]


# --- champion ---


def _champion_matches(canonical: Path, promotion: Promotion, sha: str) -> bool:
    champion = gitops.show(canonical, sha, CHAMPION_PATH)
    candidate = gitops.show(canonical, promotion.candidate_sha, promotion.candidate_path)
    return champion is not None and champion == candidate


def has_published(canonical: Path) -> bool:
    """True once any promotion has been published (the ledger then owns the champion)."""
    return bool(_published(canonical))


def champion_status(ctx: RunContext, sm: Softmax, league_id: str) -> tuple[str | None, str]:
    """(champion, why it is None). See ``current_champion``."""
    published = _published(ctx.canonical)
    on_board = sm.leaderboard_champion(sm.whoami().player_id, league_id)
    if not published:
        if on_board is None:
            return None, f"no champion known: nothing promoted and no {league_id} leaderboard entry"
        return on_board, ""
    latest = published[-1].platform_policy_version_id
    if on_board != latest:
        why = (
            f"champion unknown: the promotion ledger says {latest} but the {league_id} "
            f"leaderboard says {on_board}"
        )
        log.warning(why)
        return None, why
    return latest, ""


def current_champion(ctx: RunContext, sm: Softmax, league_id: str) -> str | None:
    """The champion's platform policy version id: the latest ``published`` promotion,
    confirmed against the Softmax leaderboard for our player. Before any promotion is
    published the leaderboard champion is taken as is (amendment A9). None when no champion
    is known, or when the ledger and the leaderboard disagree (logged; callers must refuse
    to submit)."""
    return champion_status(ctx, sm, league_id)[0]


def stage_champion(ctx: RunContext, promotion: Promotion) -> Path:
    """Copy the candidate STRATEGY at ``candidate_sha`` to the champion path in the worker's
    worktree (the worker's ``card finish`` commits it). Fetches the pushed candidate branch
    when the commit is not local (a rebuilt host)."""
    if ctx.worker is None or promotion.candidate_sha is None:
        raise PromotionError("staging the champion needs a worker worktree and a candidate_sha")
    content = gitops.show(ctx.canonical, promotion.candidate_sha, promotion.candidate_path)
    if content is None:
        gitops.git(ctx.canonical, "fetch", REMOTE, f"refs/heads/{promotion.candidate_branch}")
        content = gitops.show(ctx.canonical, promotion.candidate_sha, promotion.candidate_path)
    if content is None:
        raise PromotionError(
            f"candidate {promotion.candidate_ref} is not at {promotion.candidate_sha}"
        )
    target = ctx.worker / CHAMPION_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


# --- acquire, intent, submitted ---


def _can_take_over(canonical: Path, queue: Queue, current: Promotion | None) -> bool:
    if current is None or current.stage in ("published", "aborted"):
        return True
    return current.stage == "acquired" and _holder_terminal(canonical, queue, current)


def acquire(
    ctx: RunContext, queue: Queue, experiment: str, version: str, candidate_ref: str
) -> bool:
    """Take the promotion lock for the calling card run (C8). ``candidate_ref`` is the
    candidate STRATEGY path in this card's branch; it is stored as ``card/<Q-id>:<path>``.

    True if this run now holds a promotion in ``acquired`` (also when it already did, for
    the same experiment and version). False while another promotion holds the lock."""
    task_id, run_id = _worker_identity(ctx)
    canonical = ctx.canonical
    branch = f"card/{queue.get(task_id).spec.request_id}"
    platform_id = _platform_id(canonical, experiment, version)
    with canonical_lock(canonical):
        current = load(canonical)
        own = current is not None and _is_holder(current, task_id, run_id)
        if own and current.stage == "acquired":
            return (current.experiment, current.version) == (experiment, version)
        if not _can_take_over(canonical, queue, current):
            return False
        extra: tuple[Path, ...] = ()
        if current is not None and current.stage == "acquired":  # abandoned: close it
            at = now_iso()
            entry = {"stage": "aborted", "at": at, "note": "abandoned in acquired"}
            closed = replace(
                current, stage="aborted", updated_at=at, history=[*current.history, entry]
            )
            write_json(history_path(canonical, current.id), closed.to_dict())
            extra = (history_path(canonical, current.id),)
        at = now_iso()
        promotion = Promotion(
            id=f"P-{ULID()}",
            holder_task=task_id,
            run_id=run_id,
            board_epoch=board_epoch(ledger_of(canonical)),
            experiment=experiment,
            version=version,
            platform_policy_version_id=platform_id,
            candidate_ref=f"{branch}:{candidate_ref}",
            candidate_sha=None,
            stage="acquired",
            updated_at=at,
            history=[{"stage": "acquired", "at": at}],
        )
        _save(canonical, promotion, f"promotion {promotion.id}: acquired", extra)
    return True


def record_intent(ctx: RunContext, promotion: Promotion) -> None:
    """Push the candidate branch, commit stage ``intent`` (with ``candidate_sha``) and push
    ``main``. If either push fails, raise PromotionError and leave stage ``acquired`` (a
    failed ledger push is undone by a commit back to ``acquired``)."""
    canonical = ctx.canonical
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion.id, "acquired")
    branch = current.candidate_branch
    try:
        sha = gitops.git(canonical, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    except gitops.GitError:
        raise PromotionError(f"candidate branch {branch} does not exist") from None
    if gitops.show(canonical, sha, current.candidate_path) is None:
        raise PromotionError(f"{current.candidate_path} is not committed on {branch} ({sha})")
    if not gitops.push(canonical, REMOTE, [f"refs/heads/{branch}:refs/heads/{branch}"]):
        raise PromotionError(f"could not push the candidate branch {branch} to {REMOTE}")
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion.id, "acquired")
        intent = _move(canonical, current, "intent", candidate_sha=sha)
    if gitops.push(canonical, REMOTE, [MAIN_REFSPEC]):
        return
    with canonical_lock(canonical):
        current = load(canonical)
        if current is not None and current.id == intent.id and current.stage == "intent":
            _move(canonical, current, "acquired", "intent not pushed", candidate_sha=None)
    raise PromotionError(f"could not push the promotion ledger (main) to {REMOTE}")


def record_submitted(ctx: RunContext, promotion: Promotion, submission_id: str) -> None:
    """Commit stage ``submitted`` with the submission id, then push ``main`` (best effort: a
    failed push is retried by queue-keeper's push after every run)."""
    canonical = ctx.canonical
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion.id, "intent")
        _move(canonical, current, "submitted", submission_id=submission_id)
    if not gitops.push(canonical, REMOTE, [MAIN_REFSPEC]):
        log.warning("promotion %s submitted but the ledger push failed", promotion.id)


# --- holder role and liveness ---
#
# While a promotion is at ``intent`` or ``submitted`` the holder role belongs to the holder
# TASK, not one run: a rate-limited or reclaimed run ends and its task returns to ready
# (A8), and the retried run may finish the promotion. ``on_merge`` rebinds ``run_id`` to the
# run whose handoff merged. ``adopt`` moves the role to the latest recovery card's task.

OPEN_HANDOFF_STATES = ("committed", "validated", "merged")  # normal processing will act
CLOSED_RECEIPT_STATES = ("published", "superseded", "dropped", "rejected")
LIVE_CARD_STATES = ("pending", "claimed")


def _handoffs_of(canonical: Path, task_id: str) -> list[dict]:
    """Committed handoff records of every run of ``task_id`` at canonical HEAD."""
    directory = handoff_path(canonical, task_id, "x").parent
    rel = directory.relative_to(canonical).as_posix()
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{rel}/")
    records = []
    for name in listing.splitlines():
        record = committed_json(canonical, Path(canonical) / name)
        if record is not None:
            records.append(record)
    return records


def _task_live(canonical: Path, queue: Queue, task_id: str, epoch: str) -> bool:
    """True while ``task_id`` can still produce or advance a handoff: it has a handoff that
    normal processing will act on, or (current epoch) it is pending/claimed or has an open
    run. An unknown task in the current epoch counts as live (keep waiting, like C3)."""
    if any(h["state"] in OPEN_HANDOFF_STATES for h in _handoffs_of(canonical, task_id)):
        return True
    if epoch != board_epoch(ledger_of(canonical)):
        return False
    try:
        card = queue.get(task_id)
    except LookupError:
        return True
    return card.state in LIVE_CARD_STATES or any(not run.terminal for run in card.runs)


def _recovery_live(canonical: Path, queue: Queue, promotion: Promotion) -> bool:
    """True while the latest recovery request can still adopt and finish the promotion."""
    request_id = promotion.recovery_request
    if request_id is None:
        return False
    receipt = committed_json(canonical, receipt_path(ledger_of(canonical), request_id))
    if receipt is None:
        return True  # not admitted yet (or held back by a transient rejection)
    if receipt["state"] in CLOSED_RECEIPT_STATES:
        return False
    task = receipt["tasks"][-1]
    if receipt["state"] == "admitted" and task["board_epoch"] != board_epoch(ledger_of(canonical)):
        return True  # rebuild re-admits it (C12)
    return _task_live(canonical, queue, task["task_id"], task["board_epoch"])


def _recovery_tasks(canonical: Path, promotion: Promotion) -> set[str]:
    tasks = set()
    for request_id in promotion.recovery_requests:
        receipt = committed_json(canonical, receipt_path(ledger_of(canonical), request_id))
        for task in (receipt or {}).get("tasks", []):
            tasks.add(task["task_id"])
    return tasks


def _reached_submit(promotion: Promotion) -> bool:
    return promotion.stage in IN_FLIGHT or any(
        entry["stage"] == "submitted" for entry in promotion.history
    )


def covers_task(canonical: Path, task_id: str) -> bool:
    """True if ``task_id`` is a holder, former holder or recovery card of any promotion that
    reached submission (in flight or closed). The orphan scan leaves such tasks alone: an
    in-flight promotion's ``reconcile`` owns them, and redoing a closed one's promotion card
    would only be refused by the baseline check. Promotions aborted before submitting are
    not covered: redoing their card is a legitimate new attempt."""
    for promotion in _all_promotions(canonical):
        if not _reached_submit(promotion):
            continue
        if task_id == promotion.holder_task or task_id in promotion.former_holders:
            return True
        if task_id in _recovery_tasks(canonical, promotion):
            return True
    return False


def _covered_requests(promotion: Promotion) -> list[str]:
    """Request ids of the original holder card and every recovery attempt."""
    original = promotion.candidate_branch.removeprefix("card/")
    return [original, *promotion.recovery_requests]


def _close_covered(canonical: Path, promotion: Promotion) -> list[Path]:
    """When ``promotion`` publishes: close what it covered so nothing re-admits or redoes it.
    Its promotion-owned ``repair`` handoffs become ``superseded{by: <recording run>}``; the
    open receipts of covered requests whose tasks are dead become ``superseded{by: P-id}``;
    a recovery request never admitted gets a ``rejected`` receipt. Returns the paths written
    (the caller commits them with the ``published`` transition). Under the lock."""
    ledger = ledger_of(canonical)
    by_run = f"{promotion.holder_task}/{promotion.run_id}"
    written: list[Path] = []
    at = now_iso()
    for request_id in _covered_requests(promotion):
        path = receipt_path(ledger, request_id)
        receipt = committed_json(canonical, path)
        if receipt is None:
            if request_id in promotion.recovery_requests:
                receipt = {
                    "request_id": request_id,
                    "state": "rejected",
                    "mode": "maintenance",
                    "reason": f"promotion {promotion.id} published before admission",
                    "history": [{"state": "rejected", "at": at}],
                }
                jsonschema.validate(receipt, load_schema("receipt.json"))
                write_json(path, receipt)
                written.append(path)
            continue
        for task in receipt.get("tasks", []):
            for record in _handoffs_of(canonical, task["task_id"]):
                if record["state"] == "repair" and record.get("promotion") == promotion.id:
                    record_path = handoff_path(canonical, record["card"], record["run_id"])
                    history = [*record["history"], {"state": "superseded", "at": at}]
                    superseded = record | {
                        "state": "superseded",
                        "superseded_by": by_run,
                        "history": history,
                    }
                    write_handoff(record_path, superseded)
                    written.append(record_path)
        if receipt["state"] not in ("admitted", "handed_off"):
            continue
        tasks = receipt.get("tasks", [])
        if any(_task_live_quiet(canonical, t["task_id"]) for t in tasks):
            continue
        closed = receipt | {
            "state": "superseded",
            "superseded_by": promotion.id,
            "history": [*receipt["history"], {"state": "superseded", "at": at}],
        }
        jsonschema.validate(closed, load_schema("receipt.json"))
        write_json(path, closed)
        written.append(path)
    return written


def _task_live_quiet(canonical: Path, task_id: str) -> bool:
    """Liveness without a queue: the task has a handoff normal processing will still act on
    (the recording handoff, at publish time). Dead tasks of a published promotion have
    nothing left to do even if the board would retry them."""
    return any(h["state"] in OPEN_HANDOFF_STATES for h in _handoffs_of(canonical, task_id))


# --- merge hooks (the caller holds the canonical lock) ---

WAIT = "wait"  # leave the handoff for a later poll
REPAIR = "repair"  # mark the handoff repair, owned by the promotion (no follow-up card)
RECONCILE = "reconcile"  # the usual reconcile follow-up card


@dataclass
class GateDecision:
    kind: str  # WAIT | REPAIR | RECONCILE
    why: str
    promotion_id: str | None = None


def on_merge(ctx: RunContext, promotion: Promotion, merge_sha: str) -> None:
    """Stage ``recorded`` if the champion in ``merge_sha`` is the candidate; otherwise leave
    ``submitted`` (``merge_gate`` normally stops such a merge). Saves ``promotion`` as
    given, so a caller may pass it rebound to the merged run. Call under the lock."""
    if promotion.stage != "submitted":
        return
    if not _champion_matches(ctx.canonical, promotion, merge_sha):
        log.warning("promotion %s: merge %s lacks the candidate champion", promotion.id, merge_sha)
        return
    _move(ctx.canonical, promotion, "recorded", merge_sha=merge_sha)


def on_published(ctx: RunContext, promotion: Promotion) -> None:
    """Stage ``published`` (frees the promotion lock), closing in the same commit everything
    the promotion covered (``_close_covered``). Call under the lock."""
    if promotion.stage == "recorded":
        closed = tuple(_close_covered(ctx.canonical, promotion))
        _move(ctx.canonical, promotion, "published", extra=closed)


def merge_gate(canonical: Path, record: dict) -> GateDecision | None:
    """Decide, before merging handoff ``record``, whether the promotion lets it merge.

    A handoff of the holder task: at ``intent`` it WAITs (``reconcile`` settles the intent
    first; it never merges at ``intent``); at ``submitted`` it merges when its champion is
    the candidate, else it is a REPAIR owned by the promotion (the recovery card is the only
    follow-up). Any other handoff that changes the champion path is a RECONCILE ("champion
    mismatch"). None: merge normally."""
    current = load(canonical)
    label = f"{record['card']}/{record['run_id']}"
    if current is not None and record["card"] == current.holder_task:
        if current.stage == "intent":
            why = f"handoff {label} waits: promotion {current.id} is at intent"
            return GateDecision(WAIT, why, current.id)
        if current.stage == "submitted":
            if _champion_matches(canonical, current, record["result_sha"]):
                return None
            why = (
                f"handoff {label} holds promotion {current.id} but {CHAMPION_PATH} at "
                f"{record['result_sha']} is not the candidate {current.candidate_ref} at "
                f"{current.candidate_sha}; a promotion-recovery card will stage it"
            )
            return GateDecision(REPAIR, why, current.id)
    changed = gitops.diff_paths(canonical, record["base_sha"], record["result_sha"])
    if CHAMPION_PATH in changed:
        holder = "none" if current is None else f"{current.id} ({current.stage})"
        why = (
            f"champion mismatch: handoff {label} changes {CHAMPION_PATH} but is not the "
            f"holder of the in-flight promotion (current promotion: {holder}). The champion "
            f"changes only through a promotion; drop the champion change."
        )
        return GateDecision(RECONCILE, why)
    return None


def after_merge(ctx: RunContext, record: dict) -> None:
    """Merge hook: a merged handoff of the holder task records the promotion (rebinding
    ``run_id`` to that run). Under the lock."""
    current = load(ctx.canonical)
    if current is not None and record["card"] == current.holder_task:
        on_merge(ctx, replace(current, run_id=str(record["run_id"])), record["merge_sha"])


def after_publish(ctx: RunContext, record: dict) -> None:
    """Publish hook: publishing the handoff that recorded the promotion publishes it. Under
    the lock."""
    current = load(ctx.canonical)
    if (
        current is not None
        and record["card"] == current.holder_task
        and record.get("merge_sha") == current.merge_sha
    ):
        on_published(ctx, current)


# --- reconcile and recovery ---


def _settle_intent(ctx: RunContext, promotion: Promotion, sm: Softmax) -> Action | None:
    """``intent`` with a terminal holder run: ``submitted`` if Softmax has the submission,
    else ``aborted``. The Softmax query runs outside the lock."""
    player = sm.whoami().player_id
    found = [
        s
        for s in sm.submissions(player)
        if s["policy_version_id"] == promotion.platform_policy_version_id
    ]
    with canonical_lock(ctx.canonical):
        current = load(ctx.canonical)
        if current is None or current.id != promotion.id or current.stage != "intent":
            return None
        if found:
            submission_id = found[-1]["submission_id"]
            _move(
                ctx.canonical, current, "submitted", "found on Softmax", submission_id=submission_id
            )
            return Action("submitted", promotion.id, submission_id)
        _move(ctx.canonical, current, "aborted", "no submission on Softmax")
        return Action("aborted", promotion.id, "holder ended before submitting")


def _request_recovery(ctx: RunContext, promotion: Promotion) -> Action:
    """Write the next recovery attempt's request (P0, key ``promote-recover:<P>:<n>``) and
    commit it with the promotion's ``recovery_requests``. Under the lock."""
    canonical = ctx.canonical
    attempt = len(promotion.recovery_requests) + 1
    key = f"promote-recover:{promotion.id}:{attempt}"
    why = (
        f"Promotion {promotion.id} submitted {promotion.platform_policy_version_id} "
        f"(submission {promotion.submission_id}) but its holder {promotion.holder_task} ended "
        f"without a usable handoff (recovery attempt {attempt}). Run "
        f'"$AUTORESEARCH_CLI" promotion adopt {promotion.id}, then "$AUTORESEARCH_CLI" '
        f"promotion stage-champion {promotion.id} (copies {CHAMPION_PATH} from "
        f"{promotion.candidate_ref} at {promotion.candidate_sha}). Also carry over the "
        f"non-champion changes the promotion card made on branch "
        f"{promotion.candidate_branch} (for example experiment record updates): compare it "
        f"with main and redo them. Write the journal and finish. Do not submit again."
    )
    request = _write_runtime_request(
        canonical,
        key,
        f"Recover promotion {promotion.id}",
        "maintenance",
        _refs(promotion.experiment, promotion.version),
        why,
        "P0",
    )
    requests = [*promotion.recovery_requests, request.stem]
    updated = replace(promotion, recovery_requests=requests, updated_at=now_iso())
    _save(canonical, updated, f"promotion {promotion.id}: recovery {attempt}", (request,))
    return Action("recovery_requested", promotion.id, why, request.stem)


def _request_help(ctx: RunContext, promotion: Promotion) -> Action:
    """Recovery attempts are exhausted: publish one runtime ``help_request`` and stop
    creating cards for this promotion (``help_requested``) until an operator acts. Under the
    lock. Published before the flag is committed, so a crash in between can at worst repeat
    it; the outbox is checked first to make that rare."""
    canonical = ctx.canonical
    key = f"promote-recover-help:{promotion.id}"
    title = f"Promotion {promotion.id} needs an operator ({key})"
    if not outbox_has_title(canonical, key):
        body = (
            f"Promotion {promotion.id} submitted {promotion.platform_policy_version_id} "
            f"(submission {promotion.submission_id}) for {promotion.experiment}, but "
            f"{len(promotion.recovery_requests)} recovery cards "
            f"({', '.join(promotion.recovery_requests)}) ended without recording the champion. "
            f"No further recovery card is created and no new promotion can start until an "
            f"operator records the champion {CHAMPION_PATH} from {promotion.candidate_ref} at "
            f"{promotion.candidate_sha} or aborts the promotion."
        )
        refs = [{"uri": f"ar://experiment/{promotion.experiment}"}]
        publish_runtime(canonical, "help_request", title, body, refs, allow_no_remote=True)
    updated = replace(promotion, help_requested=True, updated_at=now_iso())
    _save(canonical, updated, f"promotion {promotion.id}: help requested")
    return Action("help_requested", promotion.id, title)


def reconcile(ctx: RunContext, queue: Queue, sm: Softmax) -> list[Action]:
    """C8 runtime reconciliation, each queue-keeper run; the caller must not hold the lock.

    1. ``intent`` with the holder run terminal (or an earlier board epoch): ``submitted``
       (submission found on Softmax) or ``aborted``. Never resubmits.
    2. ``submitted`` needs recovery unless a live holder-role run exists: the holder task
       still has a handoff normal processing will act on, or is pending/claimed (current
       epoch), or the latest recovery card is still live. A holder handoff in ``repair``,
       ``reconcile``, ``superseded``, or ``published`` without recording all count as
       "needs recovery". Then the next recovery attempt is requested (at most one live);
       after ``MAX_RECOVERY_ATTEMPTS`` one runtime ``help_request`` is published instead.
    3. ``recorded`` whose recording handoff is already ``published``: ``published``."""
    canonical = ctx.canonical
    promotion = load(canonical)
    if promotion is None or promotion.stage not in IN_FLIGHT:
        return []
    actions = []
    if promotion.stage == "intent":
        if not _holder_terminal(canonical, queue, promotion):
            return []
        action = _settle_intent(ctx, promotion, sm)
        if action is None:
            return []
        actions.append(action)
    with canonical_lock(canonical):
        current = load(canonical)
        if current is None or current.id != promotion.id:
            return actions
        if current.stage == "submitted":
            live = _task_live(canonical, queue, current.holder_task, current.board_epoch)
            if not live and not _recovery_live(canonical, queue, current):
                if len(current.recovery_requests) < MAX_RECOVERY_ATTEMPTS:
                    actions.append(_request_recovery(ctx, current))
                elif not current.help_requested:
                    actions.append(_request_help(ctx, current))
        elif current.stage == "recorded":
            for handoff in _handoffs_of(canonical, current.holder_task):
                if (
                    handoff["state"] == "published"
                    and handoff.get("merge_sha") == current.merge_sha
                ):
                    on_published(ctx, current)
                    actions.append(Action("published", current.id))
                    break
    return actions


def adopt(ctx: RunContext, promotion_id: str) -> Promotion:
    """Rebind a ``submitted`` promotion's holder to the calling card run. Only the latest
    promotion-recovery card (``recovery_request``) may adopt."""
    task_id, run_id = _worker_identity(ctx)
    card_meta = runtime_dir(ctx) / "cards" / f"{task_id}.json"
    request_id = read_json(card_meta).get("request_id") if card_meta.exists() else None
    canonical = ctx.canonical
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion_id, "submitted")
        if current.recovery_request is None or current.recovery_request != request_id:
            raise PromotionError(
                f"only the latest recovery card of {promotion_id} "
                f"(request {current.recovery_request}) may adopt it; this card is {request_id}"
            )
        if _is_holder(current, task_id, run_id):
            return current
        note = f"adopted by {task_id}/{run_id} from {current.holder_task}/{current.run_id}"
        return _move(
            canonical,
            current,
            "submitted",
            note,
            holder_task=task_id,
            run_id=run_id,
            board_epoch=board_epoch(ledger_of(canonical)),
            former_holders=[*current.former_holders, current.holder_task],
        )


def stage_champion_for(ctx: RunContext, promotion_id: str) -> Path:
    """``stage_champion`` for the calling card, which must be the holder task of promotion
    ``promotion_id`` at ``intent`` or ``submitted`` (a recovery card adopts first)."""
    task_id, _ = _worker_identity(ctx)
    current = _require(load(ctx.canonical), promotion_id, "intent", "submitted")
    if current.holder_task != task_id:
        raise PromotionError(
            f"card {task_id} does not hold promotion {promotion_id} "
            f"(holder {current.holder_task}); a recovery card must adopt it first"
        )
    return stage_champion(ctx, current)


# --- the submit path ---


def _refuse(
    ctx: RunContext, promotion: Promotion, reason: str, request_key: str = ""
) -> SubmitOutcome:
    """Abort the acquired promotion (and, with ``request_key``, write a re-evaluation request
    in the same commit)."""
    canonical = ctx.canonical
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion.id, "acquired")
        extra: tuple[Path, ...] = ()
        request_id = None
        if request_key:
            why = (
                f"Experiment {current.experiment} cannot promote: {reason}. Re-evaluate the "
                f"candidate {current.version} against the current champion."
            )
            request = _write_runtime_request(
                canonical,
                request_key,
                f"Re-evaluate {current.experiment}",
                "experimentation",
                _refs(current.experiment, current.version),
                why,
                "P1",
            )
            extra, request_id = (request,), request.stem
        _move(canonical, current, "aborted", reason, extra)
    return SubmitOutcome(False, promotion.id, reason=reason, request_id=request_id)


def submit_promotion(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    experiment: str,
    version: str,
    candidate_ref: str,
    league_id: str,
) -> SubmitOutcome:
    """The single submit path (C8): acquire -> champion == baseline -> record_intent ->
    submit -> record_submitted -> stage the champion in the worker's worktree.

    Refusals return ``ok=False`` with a reason: the lock is held by another promotion; the
    champion is unknown or is not the experiment's baseline (the promotion is aborted, and
    a mismatch also writes a re-evaluation request); a push failed (stage stays
    ``acquired``). Eligibility (decision, guardrails, N) is checked by the caller (the
    ``softmax submit`` CLI, Plan 03 Task 3.3, which passes ``queue_from_env``). The caller
    must not hold the lock."""
    if not acquire(ctx, queue, experiment, version, candidate_ref):
        return SubmitOutcome(False, reason="another promotion holds the promotion lock")
    promotion = load(ctx.canonical)
    baseline = _baseline(ctx.canonical, experiment)
    champion, why_unknown = champion_status(ctx, sm, league_id)
    if champion is None:
        return _refuse(ctx, promotion, why_unknown)
    if champion != baseline:
        reason = f"the champion {champion} is not the experiment baseline {baseline}"
        return _refuse(ctx, promotion, reason, f"reevaluate:{experiment}:{champion}")
    identity = sm.whoami()
    if identity.subject_type != "player":
        return _refuse(ctx, promotion, f"credential is a {identity.subject_type}, not a player")
    try:
        record_intent(ctx, promotion)
    except PromotionError as error:
        return SubmitOutcome(False, promotion.id, reason=str(error))
    promotion = load(ctx.canonical)
    submission_id = sm.submit(promotion.platform_policy_version_id, league_id)
    record_submitted(ctx, promotion, submission_id)
    stage_champion(ctx, load(ctx.canonical))
    return SubmitOutcome(True, promotion.id, submission_id=submission_id)
