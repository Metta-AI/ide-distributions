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
- A ``submitted`` promotion whose holder ended with no committed handoff (or with one in
  ``repair``) gets one promotion-recovery card (P0, key ``promote-recover:<P-id>``). That
  card ``adopt``s the promotion, stages the champion from ``candidate_sha``
  (``stage_champion``) and finishes; its merge drives ``recorded`` and ``published``.

Merge integration (``merge.py`` calls these under the canonical lock): ``merge_gate`` before
a merge (the holder's branch must carry the candidate at the champion path, else ``repair``;
any other branch that changes the champion path is a ``reconcile``, "champion mismatch"),
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

from ulid import ULID

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import write_request
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.admission import board_epoch
from autoresearch.runtime.handoff import handoff_path
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

log = logging.getLogger(__name__)


class PromotionError(RuntimeError):
    """A promotion step was refused; the durable state is unchanged (see the message)."""


@dataclass
class Promotion:
    """The C8 ledger record, plus the submission id, merge sha, recovery request and history."""

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
    recovery_request: str | None = None
    history: list[dict] = field(default_factory=list)

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
    """One thing ``reconcile`` did: ``submitted``, ``aborted``, ``recovery_requested`` or
    ``published``."""

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


def _published(canonical: Path) -> list[Promotion]:
    """Published promotions at canonical HEAD, oldest first (ids are ULIDs and promotions are
    serialized, so id order is publication order)."""
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{HISTORY_DIR_REL}/")
    found = []
    for rel in sorted(listing.splitlines()):
        data = committed_json(canonical, Path(canonical) / rel)
        if data is not None and data["stage"] == "published":
            found.append(Promotion.from_dict(data))
    return found


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


def current_champion(ctx: RunContext, sm: Softmax, league_id: str) -> str | None:
    """The champion's platform policy version id: the latest ``published`` promotion,
    confirmed against the Softmax leaderboard for our player. Before any promotion is
    published the leaderboard champion is taken as is. None when there is no champion, or
    when the ledger and the leaderboard disagree (logged; callers must refuse to submit)."""
    published = _published(ctx.canonical)
    on_board = sm.leaderboard_champion(sm.whoami().player_id, league_id)
    if not published:
        return on_board
    latest = published[-1].platform_policy_version_id
    if on_board != latest:
        log.warning(
            "champion unknown: promotion ledger says %s but the %s leaderboard says %s",
            latest,
            league_id,
            on_board,
        )
        return None
    return latest


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


# --- merge hooks (the caller holds the canonical lock) ---


def on_merge(ctx: RunContext, promotion: Promotion, merge_sha: str) -> None:
    """Stage ``recorded`` if the champion in ``merge_sha`` is the candidate; otherwise leave
    ``submitted`` (``merge_gate`` normally stops such a merge). Call under the lock."""
    if promotion.stage != "submitted":
        return
    if not _champion_matches(ctx.canonical, promotion, merge_sha):
        log.warning("promotion %s: merge %s lacks the candidate champion", promotion.id, merge_sha)
        return
    _move(ctx.canonical, promotion, "recorded", merge_sha=merge_sha)


def on_published(ctx: RunContext, promotion: Promotion) -> None:
    """Stage ``published``; this frees the promotion lock. Call under the lock."""
    if promotion.stage == "recorded":
        _move(ctx.canonical, promotion, "published")


def merge_gate(canonical: Path, record: dict) -> tuple[str, str] | None:
    """Before merging handoff ``record``: (``repair``, why) when it is the holder's run of a
    ``submitted`` promotion and its champion is not the candidate; (``reconcile``, why) when
    any other branch changes the champion path; else None."""
    current = load(canonical)
    label = f"{record['card']}/{record['run_id']}"
    in_flight = current is not None and current.stage == "submitted"
    if in_flight and _is_holder(current, record["card"], record["run_id"]):
        if _champion_matches(canonical, current, record["result_sha"]):
            return None
        return "repair", (
            f"Handoff {label} holds promotion {current.id} but {CHAMPION_PATH} at "
            f"{record['result_sha']} is not the candidate {current.candidate_ref} at "
            f"{current.candidate_sha}: stage the champion from the candidate and finish."
        )
    changed = gitops.diff_paths(canonical, record["base_sha"], record["result_sha"])
    if CHAMPION_PATH in changed:
        holder = "none" if current is None else f"{current.id} ({current.stage})"
        return "reconcile", (
            f"champion mismatch: handoff {label} changes {CHAMPION_PATH} but is not the "
            f"holder of the in-flight promotion (current promotion: {holder}). The champion "
            f"changes only through a promotion; drop the champion change."
        )
    return None


def after_merge(ctx: RunContext, record: dict) -> None:
    """Merge hook: ``on_merge`` when ``record`` is the holder's run. Under the lock."""
    current = load(ctx.canonical)
    if current is not None and _is_holder(current, record["card"], record["run_id"]):
        on_merge(ctx, current, record["merge_sha"])


def after_publish(ctx: RunContext, record: dict) -> None:
    """Publish hook: ``on_published`` when ``record`` is the holder's run. Under the lock."""
    current = load(ctx.canonical)
    if current is not None and _is_holder(current, record["card"], record["run_id"]):
        on_published(ctx, current)


# --- reconcile and recovery ---


def _settle_intent(ctx: RunContext, promotion: Promotion, sm: Softmax) -> Action | None:
    """``intent`` with a terminal holder: ``submitted`` if Softmax has the submission, else
    ``aborted``. The Softmax query runs outside the lock."""
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


def _request_recovery(ctx: RunContext, promotion: Promotion) -> Action | None:
    """One promotion-recovery card request per promotion, committed with the promotion's
    ``recovery_request`` pointer. Under the lock."""
    canonical = ctx.canonical
    if promotion.recovery_request is not None:
        return None
    key = f"promote-recover:{promotion.id}"
    why = (
        f"Promotion {promotion.id} submitted {promotion.platform_policy_version_id} "
        f"(submission {promotion.submission_id}) but its holder {promotion.holder_task}/"
        f"{promotion.run_id} ended without a usable handoff. Adopt promotion {promotion.id}, "
        f"stage the champion {CHAMPION_PATH} from {promotion.candidate_ref} at "
        f"{promotion.candidate_sha}, and finish. Do not submit again."
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
    updated = replace(promotion, recovery_request=request.stem, updated_at=now_iso())
    _save(canonical, updated, f"promotion {promotion.id}: recovery {request.stem}", (request,))
    return Action("recovery_requested", promotion.id, why, request.stem)


def reconcile(ctx: RunContext, queue: Queue, sm: Softmax) -> list[Action]:
    """C8 runtime reconciliation, each queue-keeper run; the caller must not hold the lock.

    With the holder terminal (run ended, or an earlier board epoch): ``intent`` becomes
    ``submitted`` (submission found on Softmax) or ``aborted``; ``submitted`` with no
    committed handoff for the holder's run, or one in ``repair``, gets one recovery card
    request; ``recorded`` whose holder handoff is already ``published`` becomes
    ``published``. Otherwise normal handoff processing drives the promotion."""
    canonical = ctx.canonical
    promotion = load(canonical)
    if promotion is None or promotion.stage not in IN_FLIGHT:
        return []
    if not _holder_terminal(canonical, queue, promotion):
        return []
    actions = []
    if promotion.stage == "intent":
        action = _settle_intent(ctx, promotion, sm)
        if action is None:
            return []
        actions.append(action)
        promotion = load(canonical)
    with canonical_lock(canonical):
        current = load(canonical)
        if current is None or current.id != promotion.id:
            return actions
        handoff = committed_json(
            canonical, handoff_path(canonical, current.holder_task, current.run_id)
        )
        state = None if handoff is None else handoff["state"]
        if current.stage == "submitted" and state in (None, "repair"):
            action = _request_recovery(ctx, current)
            if action is not None:
                actions.append(action)
        elif current.stage == "recorded" and state == "published":
            on_published(ctx, current)
            actions.append(Action("published", current.id))
    return actions


def adopt(ctx: RunContext, promotion_id: str) -> Promotion:
    """Rebind a ``submitted`` promotion's holder to the calling card run. Only the
    promotion-recovery card (the request in ``recovery_request``) may adopt."""
    task_id, run_id = _worker_identity(ctx)
    card_meta = runtime_dir(ctx) / "cards" / f"{task_id}.json"
    request_id = read_json(card_meta).get("request_id") if card_meta.exists() else None
    canonical = ctx.canonical
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion_id, "submitted")
        if current.recovery_request is None or current.recovery_request != request_id:
            raise PromotionError(
                f"only the recovery card of {promotion_id} "
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
        )


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
    champion = current_champion(ctx, sm, league_id)
    if champion is None:
        return _refuse(ctx, promotion, "champion unknown: the ledger and leaderboard disagree")
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
