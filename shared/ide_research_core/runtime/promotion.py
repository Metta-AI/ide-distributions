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
    intent (waiting for the coach) --reconcile: the retried submit succeeds, or the
        submission is found on Softmax--> submitted
    intent (waiting for the coach) --reconcile: the coach answered no--> aborted
        (DECLINED_NOTE; never promoted again)
    intent --ledger push failed (record_intent)--> acquired
    submitted --reconcile: the league rejected the submission (A9)--> rejected

Rules that make a crash at any point safe:

- Only one promotion is in flight. ``acquire`` takes over only a ``published``,
  ``aborted`` or ``rejected`` promotion, or one abandoned in ``acquired`` (its holder run is terminal, or
  its board epoch is earlier). A promotion in ``intent``, ``submitted`` or ``recorded`` is
  advanced only by the runtime (``reconcile``, merge hooks) or by its recovery card.
- Nothing is submitted before ``intent`` is committed and pushed together with the
  candidate branch, so a lost host can always find out what may have been submitted.
- ``submit_promotion`` is the single submit path. It submits only from ``acquired`` via
  ``intent``; a promotion at ``intent`` or later is never submitted again, except one the
  server refused for the coach ("Waiting for the coach", below). ``reconcile`` settles a
  crashed ``intent`` from the Softmax submissions list, never by resubmitting.
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

Kinds. An ``experiment`` promotion submits an evaluated candidate (``submit_promotion``: the
champion must be the experiment's baseline). A ``bootstrap`` promotion (amendment A14,
``bootstrap_promotion``) makes the first champion on a fresh install: the game's shipped
strategy, with no experiment (``experiment`` is None), allowed only while the league shows no
policy competing for our player. Both go through the same stages, merge hooks, reconcile and
recovery.

Waiting for the coach (Softmax hosting). On a ``cga_`` agent token the server can refuse the
submit for the coach: the IDE Controls ask before league submissions (403
``permit_required``, ``PermitRequired``), or the coach chose no league or another one
(``LeagueNotChosen``). Nothing was submitted, and the server says so, so the promotion stays at
``intent`` with ``waiting`` (``permit`` or ``league``) and ``waiting_detail`` (``{reason, ask,
state, league_id, refusals, next_retry_after, since, last_error?}``); the submit path returns
``code`` ``permit_required`` or ``league_not_chosen``. ``reconcile`` then owns it, whatever its
holder does:

- one runtime help request (``runtime_messages.permit_needed``, per promotion; or
  ``league_not_chosen``, per league and cause);
- every run, the coach's answer to the ask (``Softmax.permit_answer``, no model spend). A no
  (or a refusal the server already called declined) moves it to ``aborted`` with
  ``DECLINED_NOTE`` and one note (``coach_declined``): the slot is free, nothing is submitted
  or asked again, and a board rebuild does not request its promotion again. A declined
  bootstrap also blocks new bootstraps (``bootstrap_blocker``) until an operator runs
  ``promotion accept-champion``, as after a rejected one. A yes makes the retry due at once
  (after a failed retry, at ``next_retry_after``); while the ask waits, nothing is retried, and
  an ask still waiting ``REMINDER_AFTER`` (24 hours) after the coach was first asked
  (``waiting_detail.since``) gets one reminder help request, never more. When the answer
  cannot be read (and for a league wait) the retry waits for ``next_retry_after`` (30 minutes, doubling per
  refusal, at most 8 hours, ``protocol.retry_backoff``);
- the retry, when no champion conflict is open: the submissions list first (a submission
  found settles it: ``submitted``) and else the same submit again (the same policy version and
  league, so the server grants it against the same ask). A retry that raises records
  ``last_error`` and moves ``next_retry_after`` on by the next step, so it is not repeated
  every run.

Success moves it to ``submitted``; the holder's handoff, which waited at ``intent``, then merges
(a bootstrap: its champion path is the shipped one) or becomes a ``repair`` that a recovery card
stages (an experiment: the refused submit staged nothing), as for any ``submitted`` promotion. A
waiting intent is never aborted for a terminal holder.

Champion authority (amendment A9). The champion is whichever policy the Softmax league has
registered as competing for our player (``Softmax.leaderboard_champion``: the champion
membership, not the top of the standings), at every stage. The ledger keeps what the loop
submitted and why, but never decides the champion. ``champion_conflict`` compares what the
loop expects with what the league shows; queue-keeper pauses the agent and tells the user
when they differ, and stays paused until they agree again (a human fixes the league, or runs
``promotion accept-champion`` to accept what the league shows).

The champion is the candidate STRATEGY at ``CHAMPION_PATH``. A ``candidate_ref`` is stored as
``<branch>:<path>`` (git's ``rev:path``): the holder's card branch and the candidate STRATEGY
path in it, normally ``strategy/candidates/<E-id>/STRATEGY.md``.
"""

import datetime
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import jsonschema
from ulid import ULID

from ide_research_core.comms import runtime_messages
from ide_research_core.comms.publisher import already_published, publish_message
from ide_research_core.io import gitops
from ide_research_core.io.docs import read_json, write_json
from ide_research_core.paths import Layout
from ide_research_core.queue.base import Queue
from ide_research_core.queue.card import CardSpec
from ide_research_core.queue.requests import load_schema, write_request
from ide_research_core.records.store import RecordStore
from ide_research_core.runctx import RunContext, runtime_dir
from ide_research_core.runtime import provenance
from ide_research_core.runtime.admission import board_epoch, receipt_path
from ide_research_core.runtime.handoff import handoff_path, write_handoff
from ide_research_core.runtime.ledger import (
    canonical_lock,
    commit,
    committed_json,
    ledger_of,
    now_iso,
)
from ide_research_core.runtime.pause import pause_path
from ide_research_core.softmax_tool.protocol import (
    ANSWER_NO,
    ANSWER_USED,
    ANSWER_WAITING,
    ANSWER_YES,
    BACKOFF_BASE,
    PERMIT_DECLINED,
    REMINDER_AFTER,
    LeagueNotChosen,
    PermitRequired,
    Softmax,
    SubmissionState,
    retry_backoff,
)

CHAMPION_PATH = "strategy/STRATEGY.md"
PROMOTION_FILE_REL = ".supervisor/ledger/promotion.json"
ACCEPTED_FILE_REL = ".supervisor/ledger/champion_accepted.json"
HISTORY_DIR_REL = ".supervisor/ledger/promotions"
REMOTE = "origin"  # canonical's remote (AUTORESEARCH_WORKSPACE_REMOTE, set up by bootstrap)
IN_FLIGHT = ("intent", "submitted", "recorded")
CLOSED = ("published", "aborted", "rejected")  # terminal stages; the promotion slot is free
REACHED_SUBMIT = ("intent", "submitted", "recorded", "published")  # may have reached the league
DEFAULT_CHAMPION_GRACE = datetime.timedelta(minutes=30)
PENDING_CAP = datetime.timedelta(hours=24)  # a submission pending longer needs a human (A9)
CHAMPION_PAUSE = "champion conflict"  # the start of pause.json's reason for an A9 pause
GRACE_ENV = "AUTORESEARCH_CHAMPION_GRACE_MINUTES"
RUNTIME = "runtime"
MAX_RECOVERY_ATTEMPTS = 3
# The history note of a promotion aborted because the coach said no: it is not promoted again.
DECLINED_NOTE = "the coach declined the submission"

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
    experiment: str | None  # None for a ``bootstrap`` promotion
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
    # ``experiment``: an evaluated candidate (``submit_promotion``). ``bootstrap``: the first
    # champion on a fresh install, the game's starter policy, with no experiment (A14,
    # ``bootstrap_promotion``). Records written before kinds existed are ``experiment``.
    kind: str = "experiment"
    # The league's champion when this promotion was submitted (None: the league showed none,
    # as for a bootstrap). While the submission is in flight the league may still show it
    # (A9 grace window, ``champion_conflict``). None in records written before A9's revision.
    replaces: str | None = None
    # ``permit`` or ``league`` while an ``intent`` waits for the coach (module docstring,
    # "Waiting for the coach"), with ``waiting_detail``; None otherwise.
    waiting: str | None = None
    waiting_detail: dict | None = None

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
    ``help_requested``, ``published``, ``rejected`` or ``waiting`` (a retried submit the
    server refused for the coach again)."""

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
    code: str = ""  # a machine-readable refusal reason, when the caller should see one


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


def _refs(experiment: str | None, version: str) -> list[str]:
    """The experiment (none for a bootstrap promotion) and version refs."""
    refs = [f"ar://experiment/{experiment}"] if experiment else []
    return [*refs, f"ar://version/{version}"]


# --- champion ---


def _champion_matches(canonical: Path, promotion: Promotion, sha: str) -> bool:
    champion = gitops.show(canonical, sha, CHAMPION_PATH)
    candidate = gitops.show(canonical, promotion.candidate_sha, promotion.candidate_path)
    return champion is not None and champion == candidate


def champion_status(ctx: RunContext, sm: Softmax, league_id: str) -> tuple[str | None, str]:
    """(champion, why it is None). See ``current_champion``."""
    on_board = sm.leaderboard_champion(sm.whoami().player_id, league_id)
    if on_board is None:
        return None, f"no champion: the {league_id} league shows no policy competing for us"
    return on_board, ""


def current_champion(ctx: RunContext, sm: Softmax, league_id: str) -> str | None:
    """The champion's platform policy version id: the policy the league has registered as
    competing for our player (amendment A9), at every stage. None when the league shows none.
    The promotion ledger is never consulted; ``champion_conflict`` reports a disagreement."""
    return champion_status(ctx, sm, league_id)[0]


@dataclass(frozen=True)
class ChampionConflict:
    """What the loop expected the league to show (``expected``; None: no policy) and what it
    shows (``shown``), with an explanation for the log and the pause record (``detail``)."""

    expected: str | None
    shown: str | None
    detail: str
    waited_out: bool = False  # the league still shows the replaced policy after the grace


def champion_grace(env) -> datetime.timedelta:
    """The A9 grace window: ``AUTORESEARCH_CHAMPION_GRACE_MINUTES`` (a positive number) when
    set, else 30 minutes. On ``terminal.env_passthrough``: queue-keeper (a cron job) reads it."""
    value = (env.get(GRACE_ENV) or "").strip()
    if not value:
        return DEFAULT_CHAMPION_GRACE
    try:
        minutes = float(value)
    except ValueError:
        minutes = 0.0
    if not minutes > 0 or minutes == float("inf"):
        raise ValueError(f"{GRACE_ENV} must be a positive number of minutes, got {value!r}")
    return datetime.timedelta(minutes=minutes)


def accepted_path(canonical: Path) -> Path:
    return Path(canonical) / ACCEPTED_FILE_REL


def _stage_time(promotion: Promotion, stage: str) -> datetime.datetime | None:
    for entry in reversed(promotion.history):
        if entry.get("stage") == stage:
            return datetime.datetime.fromisoformat(entry["at"])
    return None


def latest_reached(canonical: Path) -> Promotion | None:
    """The latest promotion that may have reached the league: the current one if it got to
    ``intent`` or later, else the latest published one."""
    current = load(canonical)
    if current is not None and current.stage in REACHED_SUBMIT:
        return current
    published = _published(canonical)
    return published[-1] if published else None


def _shown(policy: str | None) -> str:
    return policy or "no policy"


def champion_conflict(
    canonical: Path,
    on_board: str | None,
    now: datetime.datetime,
    grace: datetime.timedelta = DEFAULT_CHAMPION_GRACE,
    submission: SubmissionState | None = None,
    competing_since: datetime.datetime | None = None,
) -> ChampionConflict | None:
    """Compare what the loop expects with the league's champion ``on_board`` (amendment A9).
    The league is right either way; a conflict means a human must look (queue-keeper pauses).

    The expectation comes from the latest promotion that may have reached the league
    (``latest_reached``), unless a human accepted the league's champion after it
    (``promotion accept-champion``, ledger ``champion_accepted.json``), which then is the
    expectation (the promotion's own policy, once the league shows it, also agrees). With no
    promotion and nothing accepted there is no expectation (a fresh install, or a champion
    submitted before the loop ran: the league is taken as is).

    When the league shows the policy that promotion replaced (``Promotion.replaces``), what
    counts is where our submission stands (``submission``, ``Softmax.submission_state``):

    - ``pending`` (submitted, qualifying, held): no conflict; it is not admitted yet. Neither
      at ``intent`` (reconcile settles that from the submissions list).
    - ``competing`` but not champion: admitted, and an admitted membership becomes champion
      by default, so the league gets ``grace`` from ``competing_since`` (when the runtime
      first saw it admitted) to show it; after that it is a conflict (``waited_out``).
    - ``rejected`` (rejected, withdrawn, disqualified): a real outcome, not a conflict; the
      loop now expects the replaced policy (queue-keeper tells the user once).
    - ``unknown`` (neither list names it, or no state was read): ``grace`` from the submit.

    Any other policy on the league is a conflict at once, including one submitted by hand."""
    basis = latest_reached(canonical)
    accepted = committed_json(canonical, accepted_path(canonical))
    if accepted is not None and accepted.get("after_promotion") == (basis.id if basis else None):
        expected = accepted.get("policy_version_id")
        if on_board == expected or (basis is not None and on_board == basis.platform_policy_version_id):
            return None
        return ChampionConflict(
            expected,
            on_board,
            f"a human accepted {_shown(expected)} as the champion, but the league now shows "
            f"{_shown(on_board)}",
        )
    if basis is None:
        return None
    new, old = basis.platform_policy_version_id, basis.replaces
    if on_board == new:
        return None
    stage = submission.stage if submission is not None else "unknown"
    if stage == "rejected":
        if on_board == old:
            return None
        return ChampionConflict(
            old,
            on_board,
            f"the league did not take {new} ({submission.detail}), so the loop expects "
            f"{_shown(old)} to stay the champion, but the league shows {_shown(on_board)}",
        )
    if on_board != old:
        return ChampionConflict(
            new,
            on_board,
            f"the loop's latest promotion submitted {new}, but the league shows "
            f"{_shown(on_board)} (a policy submitted by hand, a retired membership, or a "
            "disqualified champion)",
        )
    if basis.stage == "intent" or stage == "pending":
        return None
    since = (competing_since or now) if stage == "competing" else _stage_time(basis, "submitted")
    if since is None or now - since < grace:
        return None
    minutes = int((now - since).total_seconds() // 60)
    what = "admitted" if stage == "competing" else "submitted"
    return ChampionConflict(
        new,
        on_board,
        f"the league {what} {new} {minutes} minutes ago, but still shows {_shown(on_board)} "
        f"as our champion (the grace window is {int(grace.total_seconds() // 60)} minutes)",
        waited_out=True,
    )


@dataclass(frozen=True)
class LeagueCheck:
    """One read of the league against the loop (``league_check``)."""

    shown: str | None  # the league's champion for us
    basis: Promotion | None  # the latest promotion that may have reached the league
    submission: SubmissionState | None  # its state, read only when the league does not show it
    conflict: ChampionConflict | None


def watch_path(canonical: Path) -> Path:
    """Disposable runtime state: when the runtime first saw a promotion's policy admitted."""
    return Path(canonical) / ".supervisor" / "runtime" / "champion_watch.json"


def _competing_since(
    canonical: Path, promotion_id: str, submission: SubmissionState | None,
    now: datetime.datetime, record: bool,
) -> datetime.datetime | None:
    path = watch_path(canonical)
    try:
        seen = read_json(path) if path.exists() else {}
    except ValueError:
        seen = {}
    if submission is None or submission.stage != "competing":
        if record and promotion_id in seen:
            write_json(path, {k: v for k, v in seen.items() if k != promotion_id})
        return None
    if promotion_id in seen:
        return datetime.datetime.fromisoformat(seen[promotion_id])
    if record:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {**seen, promotion_id: now.isoformat()})
    return now


def league_check(
    ctx: RunContext,
    sm: Softmax,
    league_id: str,
    now: datetime.datetime,
    grace: datetime.timedelta = DEFAULT_CHAMPION_GRACE,
    record: bool = False,
) -> LeagueCheck:
    """Read the league's champion and, when it is not the latest promotion's policy, where
    that submission stands, and compare (``champion_conflict``). queue-keeper passes
    ``record=True`` to keep ``champion_watch.json`` (when an admission was first seen); every
    other caller only reads it."""
    shown = current_champion(ctx, sm, league_id)
    basis = latest_reached(ctx.canonical)
    submission = None
    since = None
    if basis is not None and shown != basis.platform_policy_version_id:
        player = sm.whoami().player_id
        submission = sm.submission_state(player, league_id, basis.platform_policy_version_id)
        since = _competing_since(ctx.canonical, basis.id, submission, now, record)
    conflict = champion_conflict(ctx.canonical, shown, now, grace, submission, since)
    return LeagueCheck(shown, basis, submission, conflict)


def open_conflict(ctx: RunContext, sm: Softmax, league_id: str) -> str | None:
    """Why no submission may start now (A9), or None: the league and the loop disagree
    (``league_check``, grace from the environment, default on a malformed value), or the agent
    is paused for a champion conflict that no human has resolved yet."""
    pause_file = pause_path(ledger_of(ctx.canonical))
    if pause_file.exists():
        reason = str(read_json(pause_file).get("reason", ""))
        if reason.startswith(CHAMPION_PAUSE):
            return f"the agent is paused for a champion conflict: {reason}"
    try:
        grace = champion_grace(os.environ)
    except ValueError:
        grace = DEFAULT_CHAMPION_GRACE
    now = datetime.datetime.now(datetime.UTC)
    conflict = league_check(ctx, sm, league_id, now, grace).conflict
    return None if conflict is None else f"champion conflict: {conflict.detail}"


def baseline_problem(
    ctx: RunContext, sm: Softmax, league_id: str, baseline: str | None
) -> tuple[str, str] | None:
    """Why an experiment may not use ``baseline`` now, as ``(reason, message)``, or None when
    it is the current champion. An experiment designed or launched against any other policy
    can never promote (``submit_promotion`` requires champion == baseline), so ``stats
    design`` and ``softmax xp create`` refuse it. Reasons: ``champion_conflict``
    (``open_conflict``), ``champion_unknown`` (the league shows no champion: the starter-policy
    bootstrap comes first, A14), ``baseline_not_champion`` (any other policy, such as a
    leftover uploaded version)."""
    conflict = open_conflict(ctx, sm, league_id)
    if conflict is not None:
        message = (
            f"{conflict}. Do not design or launch until a human resolves it: record it and "
            "finish blocked"
        )
        return "champion_conflict", message
    champion, why_none = champion_status(ctx, sm, league_id)
    if champion is None:
        message = (
            f"{why_none}. There is no baseline yet: make the first champion with the "
            'starter-policy bootstrap (submit skill, section "Bootstrap card"; `softmax '
            "bootstrap-champion`), then design against it. An uploaded version that is not "
            "the champion is never a baseline"
        )
        return "champion_unknown", message
    if baseline != champion:
        message = (
            f"baseline {baseline!r} is not the current champion {champion}: an experiment "
            "promotes only against the champion. Design against the champion "
            "(baseline_version and the experiment's baseline); a leftover uploaded version "
            "is never a baseline"
        )
        return "baseline_not_champion", message
    return None


def bootstrap_blocker(canonical: Path) -> str | None:
    """Why a bootstrap may not run although the league shows no champion (A9, A14), or None.
    A bootstrap is for a fresh install: once a promotion of the loop may have reached the
    league, "no champion" means that champion was lost, which a human must look at, unless a
    human already accepted "no champion" after it (``promotion accept-champion``). After the
    league rejected a bootstrap, a new bootstrap waits for a human too (it would resubmit
    the same starter policy), unless the human ran ``accept-champion`` after the rejection.
    The same holds after the coach declined a bootstrap's submission (``declined``): a new
    bootstrap would only ask the coach again."""
    accepted = committed_json(canonical, accepted_path(canonical))
    current = load(canonical)
    closed_bootstrap = (
        current is not None
        and current.kind == "bootstrap"
        and (current.stage == "rejected" or declined(current.to_dict()))
    )
    if closed_bootstrap and (accepted is None or accepted.get("after_rejected") != current.id):
        if current.stage == "rejected":
            return (
                f"the league rejected the starter policy of promotion {current.id}; a human "
                "decides what to do (promotion accept-champion lets the agent try again)"
            )
        return (
            f"the coach declined the submission of the starter policy (promotion {current.id}); "
            "it waits for the coach (promotion accept-champion lets the agent try again)"
        )
    basis = latest_reached(canonical)
    if basis is None:
        return None
    if (
        accepted is not None
        and accepted.get("after_promotion") == basis.id
        and accepted.get("policy_version_id") is None
    ):
        return None
    return (
        f"promotion {basis.id} already submitted {basis.platform_policy_version_id}; the league "
        "showing no champion means it was lost, not a fresh install (a human resolves it)"
    )


def accept_champion(ctx: RunContext, sm: Softmax, league_id: str) -> dict:
    """Operator resolution of a champion conflict (A9): accept the policy the league shows now
    (possibly none) as what the loop expects, until a newer promotion reaches the league
    (an in-flight promotion keeps it: the acceptance is tied to the latest one that may have
    reached the league). After the league rejected a bootstrap, or the coach declined its
    submission, it also lets a new bootstrap run (``after_rejected``).
    Commits ``champion_accepted.json``; queue-keeper resumes on its next clean check."""
    on_board = sm.leaderboard_champion(sm.whoami().player_id, league_id)
    canonical = ctx.canonical
    with canonical_lock(canonical):
        basis = latest_reached(canonical)
        current = load(canonical)
        record = {
            "policy_version_id": on_board,
            "after_promotion": basis.id if basis else None,
            "after_rejected": (
                current.id
                if current and (current.stage == "rejected" or declined(current.to_dict()))
                else None
            ),
            "league_id": league_id,
            "accepted_at": now_iso(),
        }
        path = accepted_path(canonical)
        write_json(path, record)
        commit(canonical, [path], f"champion accepted from the league: {_shown(on_board)}")
    return record


def version_dir(version: str) -> str:
    """The repo-relative directory of version record ``version`` (record and events)."""
    return (Layout(Path()).versions / version).as_posix()


def _restore_version(ctx: RunContext, promotion: Promotion) -> None:
    """Copy a bootstrap promotion's version record and events from ``candidate_sha`` into
    the worker's worktree. The bootstrap card committed them on its branch before intent;
    only that branch has them, so a recovery card must carry them over (A11)."""
    directory = version_dir(promotion.version)
    listing = gitops.git(
        ctx.canonical, "ls-tree", "-r", "--name-only", promotion.candidate_sha, "--", directory
    )
    for rel in listing.splitlines():
        target = ctx.worker / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gitops.show(ctx.canonical, promotion.candidate_sha, rel))


def stage_champion(ctx: RunContext, promotion: Promotion) -> Path:
    """Copy the candidate STRATEGY at ``candidate_sha`` to the champion path in the worker's
    worktree (the worker's ``card finish`` commits it); for a bootstrap promotion also restore
    its version record from that commit (``_restore_version``). Fetches the pushed candidate
    branch when the commit is not local (a rebuilt host)."""
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
    if promotion.kind == "bootstrap":
        _restore_version(ctx, promotion)
    return target


# --- acquire, intent, submitted ---


def _can_take_over(canonical: Path, queue: Queue, current: Promotion | None) -> bool:
    if current is None or current.stage in CLOSED:
        return True
    return current.stage == "acquired" and _holder_terminal(canonical, queue, current)


def acquire(
    ctx: RunContext, queue: Queue, experiment: str, version: str, candidate_ref: str
) -> bool:
    """Take the promotion lock for the calling card run (C8). ``candidate_ref`` is the
    candidate STRATEGY path in this card's branch; it is stored as ``card/<Q-id>:<path>``.

    True if this run now holds a promotion in ``acquired`` (also when it already did, for
    the same experiment and version). False while another promotion holds the lock."""
    platform_id = _platform_id(ctx.canonical, experiment, version)
    return _take_lock(ctx, queue, "experiment", experiment, version, platform_id, candidate_ref)


def _take_lock(
    ctx: RunContext,
    queue: Queue,
    kind: str,
    experiment: str | None,
    version: str,
    platform_id: str,
    candidate_ref: str,
) -> bool:
    """``acquire`` for a promotion of ``kind`` (see ``acquire``)."""
    task_id, run_id = _worker_identity(ctx)
    canonical = ctx.canonical
    branch = f"card/{queue.get(task_id).spec.request_id}"
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
            kind=kind,
        )
        _save(canonical, promotion, f"promotion {promotion.id}: acquired", extra)
    return True


def record_intent(ctx: RunContext, promotion: Promotion, replaces: str | None = None) -> None:
    """Push the candidate branch, commit stage ``intent`` (with ``candidate_sha`` and
    ``replaces``, the league's champion at submit time) and push ``main``. If either push fails, raise PromotionError and leave stage ``acquired`` (a
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
        intent = _move(canonical, current, "intent", candidate_sha=sha, replaces=replaces)
    with canonical_lock(canonical):
        pushed = provenance.push_main(canonical, REMOTE)
    if pushed:
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
    with canonical_lock(canonical):
        pushed = provenance.push_main(canonical, REMOTE)
    if not pushed:
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
    reached submission (in flight or closed), or that the coach declined. The orphan scan
    leaves such tasks alone: an in-flight promotion's ``reconcile`` owns them, redoing a closed
    one's promotion card would only be refused by the baseline check, and redoing a declined
    one would only ask the coach again. Promotions aborted before submitting for any other
    reason are not covered: redoing their card is a legitimate new attempt."""
    for promotion in _all_promotions(canonical):
        if not (_reached_submit(promotion) or declined(promotion.to_dict())):
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


def _close_covered(
    canonical: Path, promotion: Promotion, outcome: str = "published"
) -> list[Path]:
    """When ``promotion`` publishes (or the league rejects it, ``outcome="rejected"``): close
    what it covered so nothing re-admits or redoes it.
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
                    "reason": f"promotion {promotion.id} {outcome} before admission",
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


def coach_wait_message(
    promotion: Promotion, reminder: bool = False
) -> runtime_messages.RuntimeMessage:
    """The one help request about a promotion waiting for the coach: its permit ask (per
    promotion and ask; ``reminder``: the one reminder, ``REMINDER_AFTER`` after it), or the
    league choice (per league and cause)."""
    detail = promotion.waiting_detail or {}
    league = detail.get("league_id") or ""
    refs = [{"uri": _refs(promotion.experiment, promotion.version)[0]}]
    if promotion.waiting == "league":
        minutes = int(BACKOFF_BASE.total_seconds() // 60)
        reason = detail.get("reason") or ""
        return runtime_messages.league_not_chosen("submit", league, reason, minutes, refs)
    asks = [detail["ask"]] if detail.get("ask") else []
    return runtime_messages.permit_needed(
        "submit", promotion.id, 1, asks, league, refs=refs, reminder=reminder
    )


def _ask_coach(canonical: Path, promotion: Promotion, reminder: bool = False) -> list[Action]:
    """Publish ``coach_wait_message`` unless it already was. Takes the lock."""
    message = coach_wait_message(promotion, reminder)
    with canonical_lock(canonical):
        if already_published(canonical, message):
            return []
        publish_message(canonical, message, allow_no_remote=True)
    return [Action("help_requested", promotion.id, message.title)]


def _coach_answer(sm: Softmax, promotion: Promotion) -> str | None:
    """The coach's answer to the waiting promotion's permit ask; None for a league wait or
    when it cannot be read. A refusal the server already called declined is a no."""
    detail = promotion.waiting_detail or {}
    if promotion.waiting != "permit":
        return None
    if detail.get("state") == PERMIT_DECLINED:
        return ANSWER_NO
    if not detail.get("ask"):
        return None
    try:
        return sm.permit_answer(detail["ask"])
    except Exception:  # noqa: BLE001 - an unreadable answer only means "unknown"
        return None


def declined(record: dict) -> bool:
    """A promotion record (as stored) that ended because the coach said no."""
    history = record.get("history") or []
    return record.get("stage") == "aborted" and bool(history) and (
        history[-1].get("note") == DECLINED_NOTE
    )


def _decline(canonical: Path, promotion: Promotion) -> list[Action]:
    """The coach said no: ``aborted`` (the slot is free; nothing was submitted), one note.
    Under the lock."""
    _move(
        canonical,
        promotion,
        "aborted",
        DECLINED_NOTE,
        waiting=None,
        waiting_detail=None,
    )
    message = runtime_messages.coach_declined(
        "bootstrap" if promotion.kind == "bootstrap" else "submit",
        promotion.id,
        refs=[{"uri": _refs(promotion.experiment, promotion.version)[0]}],
    )
    if not already_published(canonical, message):
        publish_message(canonical, message, allow_no_remote=True)
    return [Action("aborted", promotion.id, DECLINED_NOTE)]


def _retry_due(sm: Softmax, promotion: Promotion, now: datetime.datetime) -> tuple[bool, str]:
    """(due, answer): retry the submit now? A yes (or a used yes) is due at once, or after
    ``next_retry_after`` once a retry failed (``last_error``); an ask still waiting never is;
    an unreadable answer, or a league wait, after ``next_retry_after``."""
    detail = promotion.waiting_detail or {}
    after = datetime.datetime.fromisoformat(detail["next_retry_after"])
    answer = _coach_answer(sm, promotion)
    if answer in (ANSWER_YES, ANSWER_USED):
        # At once, unless a retry after the yes failed: then on the backoff it set (I1).
        return not detail.get("last_error") or now >= after, answer
    if answer in (ANSWER_NO, ANSWER_WAITING):
        return False, answer
    return now >= after, "unknown"


def _wait_for_coach(
    ctx: RunContext, promotion: Promotion, sm: Softmax, now: datetime.datetime
) -> list[Action]:
    """An ``intent`` promotion the server refused for the coach (module docstring): one help
    request; the coach's answer read every run; on a no, ``aborted``; once due (a yes, or the
    backoff when the answer cannot be read) and no champion conflict is open, the submissions
    list (a submission found settles it) and else the same submit again. A retry that raises
    is recorded and moves the retry time on, so it is not repeated every run. Network calls
    run outside the lock."""
    canonical = ctx.canonical
    actions = _ask_coach(canonical, promotion)
    detail = promotion.waiting_detail or {}
    league_id = detail["league_id"]
    due, answer = _retry_due(sm, promotion, now)
    if answer == ANSWER_NO:
        with canonical_lock(canonical):
            current = load(canonical)
            if current is None or current.id != promotion.id or current.stage != "intent":
                return actions
            return [*actions, *_decline(canonical, current)]
    if answer == ANSWER_WAITING and _waited(detail, now):
        actions += _ask_coach(canonical, promotion, reminder=True)
    if not due:
        return actions
    try:
        if open_conflict(ctx, sm, league_id) is not None:
            return actions
        player = sm.whoami().player_id
        found = [
            s
            for s in sm.submissions(player)
            if s["policy_version_id"] == promotion.platform_policy_version_id
            and s["league_id"] == league_id
        ]
        submitted = (
            found[-1]["submission_id"]
            if found
            else sm.submit(promotion.platform_policy_version_id, league_id)
        )
    except Exception as error:
        _record_retry_error(canonical, promotion, now, error)
        raise
    with canonical_lock(canonical):
        current = load(canonical)
        if current is None or current.id != promotion.id or current.stage != "intent":
            return actions
        if isinstance(submitted, str):
            note = "found on Softmax" if found else "submitted after the coach's answer"
            _move(
                canonical,
                current,
                "submitted",
                note,
                submission_id=submitted,
                waiting=None,
                waiting_detail=None,
            )
            actions.append(Action("submitted", promotion.id, submitted))
            return actions
        fields = _waiting_fields(current, submitted, league_id, now)
        still = replace(current, updated_at=now_iso(), **fields)
        _save(canonical, still, f"promotion {promotion.id}: still waiting for the coach")
    actions.append(Action("waiting", promotion.id, submitted.reason))
    # The other kind of wait (a permit after the league, or back) is a new help request.
    return [*actions, *_ask_coach(canonical, still)]


def _waited(detail: dict, now: datetime.datetime) -> bool:
    """The ask has waited ``REMINDER_AFTER`` since the coach was first asked."""
    since = detail.get("since")
    return bool(since) and now - datetime.datetime.fromisoformat(since) >= REMINDER_AFTER


def _record_retry_error(
    canonical: Path, promotion: Promotion, now: datetime.datetime, error: Exception
) -> None:
    """A retried submit (or its reads) raised: record it and move ``next_retry_after`` on by
    the next backoff step, so a failure that repeats is retried on the backoff, not every
    run."""
    with canonical_lock(canonical):
        current = load(canonical)
        if current is None or current.id != promotion.id or current.stage != "intent":
            return
        detail = dict(current.waiting_detail or {})
        refusals = int(detail.get("refusals", 0)) + 1
        detail.update(
            refusals=refusals,
            next_retry_after=(now + retry_backoff(refusals)).isoformat(timespec="seconds"),
            last_error=f"{type(error).__name__}: {error}"[-500:],
        )
        updated = replace(current, updated_at=now_iso(), waiting_detail=detail)
        _save(canonical, updated, f"promotion {promotion.id}: retry failed, waiting longer")


def _request_recovery(ctx: RunContext, promotion: Promotion) -> Action:
    """Write the next recovery attempt's request (P0, key ``promote-recover:<P>:<n>``) and
    commit it with the promotion's ``recovery_requests``. Under the lock."""
    canonical = ctx.canonical
    attempt = len(promotion.recovery_requests) + 1
    key = f"promote-recover:{promotion.id}:{attempt}"
    if promotion.kind == "bootstrap":
        carry_over = (
            f"stage-champion also restores the champion's version record "
            f"{version_dir(promotion.version)} from that commit (if it is missing after "
            f"stage-champion, run git checkout {promotion.candidate_sha} -- "
            f"{version_dir(promotion.version)}). Compare branch {promotion.candidate_branch} "
            f"with main and redo any other change it made. If the holder card was an "
            f"experiment card, request that experiment again (queue request create, with the "
            f"hypothesis ref)."
        )
    else:
        carry_over = (
            f"Also carry over the non-champion changes the promotion card made on branch "
            f"{promotion.candidate_branch} (for example experiment record updates): compare "
            f"it with main and redo them."
        )
    why = (
        f"Promotion {promotion.id} submitted {promotion.platform_policy_version_id} "
        f"(submission {promotion.submission_id}) but its holder {promotion.holder_task} ended "
        f"without a usable handoff (recovery attempt {attempt}). Run "
        f'"$AUTORESEARCH_CLI" promotion adopt {promotion.id}, then "$AUTORESEARCH_CLI" '
        f"promotion stage-champion {promotion.id} (copies {CHAMPION_PATH} from "
        f"{promotion.candidate_ref} at {promotion.candidate_sha}). {carry_over} Write the "
        f"journal and finish. Do not submit again."
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
    message = runtime_messages.promotion_stuck(
        promotion_id=promotion.id,
        recovery_requests=list(promotion.recovery_requests),
        champion_path=CHAMPION_PATH,
        submission_id=promotion.submission_id,
        policy_version_id=promotion.platform_policy_version_id,
        candidate_ref=promotion.candidate_ref,
        candidate_sha=promotion.candidate_sha,
        refs=[{"uri": _refs(promotion.experiment, promotion.version)[0]}],
    )
    if not already_published(canonical, message):
        publish_message(canonical, message, allow_no_remote=True)
    updated = replace(promotion, help_requested=True, updated_at=now_iso())
    _save(canonical, updated, f"promotion {promotion.id}: help requested")
    return Action("help_requested", promotion.id, message.title)


def _league_view(
    ctx: RunContext, sm: Softmax, league_id: str, promotion: Promotion
) -> tuple[bool, SubmissionState | None]:
    """(undecided, the submission's state) for a ``submitted`` promotion. Undecided: the
    league neither shows its policy, nor rejected it, nor disagrees with the loop (still
    qualifying, or within the grace window), so a recovery card could only finish ``partial``
    (A9, A11)."""
    try:
        grace = champion_grace(os.environ)
    except ValueError:
        grace = DEFAULT_CHAMPION_GRACE
    check = league_check(ctx, sm, league_id, datetime.datetime.now(datetime.UTC), grace)
    rejected = check.submission is not None and check.submission.stage == "rejected"
    undecided = (
        check.shown != promotion.platform_policy_version_id
        and check.conflict is None
        and not rejected
    )
    return undecided, check.submission


def rejection_message(promotion: Promotion, league_id: str, detail: str):
    """The one message about a promotion the league did not take (A9)."""
    return runtime_messages.submission_rejected(
        league_id,
        promotion.id,
        promotion.platform_policy_version_id,
        promotion.replaces,
        detail,
        bootstrap=promotion.kind == "bootstrap",
    )


def _settle_rejected(
    ctx: RunContext, promotion: Promotion, league_id: str, detail: str
) -> Action:
    """The league rejected a ``submitted`` promotion's policy: publish the one message, then
    move it to ``rejected`` (terminal: the slot is free) with what it covered closed, as on
    publish. Nothing is resubmitted. Under the lock."""
    canonical = ctx.canonical
    message = rejection_message(promotion, league_id, detail)
    if not already_published(canonical, message):
        publish_message(canonical, message, allow_no_remote=True)
    closed = tuple(_close_covered(canonical, promotion, outcome="rejected"))
    _move(canonical, promotion, "rejected", f"the league rejected it: {detail}", closed)
    return Action("rejected", promotion.id, detail)


def _pending_too_long(
    ctx: RunContext, promotion: Promotion, league_id: str, submission: SubmissionState | None
) -> Action | None:
    """A submission still pending ``PENDING_CAP`` after the submit gets one help_request
    (the league may be holding it for an operator). Under the lock."""
    submitted_at = _stage_time(promotion, "submitted")
    if submission is None or submission.stage != "pending" or submitted_at is None:
        return None
    if datetime.datetime.now(datetime.UTC) - submitted_at < PENDING_CAP:
        return None
    message = runtime_messages.submission_pending(
        league_id,
        promotion.id,
        promotion.platform_policy_version_id,
        submission.detail,
        int(PENDING_CAP.total_seconds() // 3600),
    )
    if already_published(ctx.canonical, message):
        return None
    publish_message(ctx.canonical, message, allow_no_remote=True)
    return Action("help_requested", promotion.id, message.title)


def reconcile(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    league_id: str | None = None,
    now: datetime.datetime | None = None,
) -> list[Action]:
    """C8 runtime reconciliation, each queue-keeper run; the caller must not hold the lock.

    1. ``intent`` with the holder run terminal (or an earlier board epoch): ``submitted``
       (submission found on Softmax) or ``aborted``. Never resubmits. An ``intent`` waiting
       for the coach is ``_wait_for_coach``'s instead, whatever its holder does (module
       docstring): the help request, and the retried submit when due (``now``, default the
       current time).
    2. ``submitted`` needs recovery unless a live holder-role run exists: the holder task
       still has a handoff normal processing will act on, or is pending/claimed (current
       epoch), or the latest recovery card is still live. A holder handoff in ``repair``,
       ``reconcile``, ``superseded``, or ``published`` without recording all count as
       "needs recovery". Then the next recovery attempt is requested (at most one live);
       after ``MAX_RECOVERY_ATTEMPTS`` one runtime ``help_request`` is published instead.
       With ``league_id`` (queue-keeper passes the game's league), no attempt is requested
       while the league has not decided (``_league_view``: our submission is still
       qualifying or the league is within the grace window), so waiting on the league uses
       up no attempts (A9, A11); a submission pending longer than ``PENDING_CAP`` gets one
       help_request. When the league rejected the submission (rejected, withdrawn,
       disqualified), the promotion is settled at once, whatever its holder is doing:
       ``rejected`` (terminal, the slot is free), covered requests closed as on publish, and
       one runtime message (``rejection_message``) that says what the human can do.
    3. ``recorded`` whose recording handoff is already ``published``: ``published``."""
    canonical = ctx.canonical
    promotion = load(canonical)
    if promotion is None or promotion.stage not in IN_FLIGHT:
        return []
    actions = []
    if promotion.stage == "intent" and promotion.waiting is not None:
        now = now or datetime.datetime.now(datetime.UTC)
        actions = _wait_for_coach(ctx, promotion, sm, now)
        if load(canonical).stage == "intent":
            return actions
    elif promotion.stage == "intent":
        if not _holder_terminal(canonical, queue, promotion):
            return []
        action = _settle_intent(ctx, promotion, sm)
        if action is None:
            return []
        actions.append(action)
    undecided, submission = False, None
    if league_id is not None and load(canonical).stage == "submitted":
        # Network reads outside the lock.
        undecided, submission = _league_view(ctx, sm, league_id, load(canonical))
    with canonical_lock(canonical):
        current = load(canonical)
        if current is None or current.id != promotion.id:
            return actions
        if current.stage == "submitted" and submission is not None:
            if submission.stage == "rejected":
                actions.append(_settle_rejected(ctx, current, league_id, submission.detail))
                return actions
            pending = _pending_too_long(ctx, current, league_id, submission)
            if pending is not None:
                actions.append(pending)
        if current.stage == "submitted":
            live = _task_live(canonical, queue, current.holder_task, current.board_epoch)
            if not live and not undecided and not _recovery_live(canonical, queue, current):
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
    ctx: RunContext, promotion: Promotion, reason: str, request_key: str = "", code: str = ""
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
    return SubmitOutcome(False, promotion.id, reason=reason, request_id=request_id, code=code)


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
    must not hold the lock.

    An experiment or version whose promotion the coach declined is refused before anything
    else (code ``coach_declined``): submitting it again would only file a new ask for the
    answer already given."""
    for earlier in _all_promotions(ctx.canonical):
        if declined(earlier.to_dict()) and (
            earlier.experiment == experiment or earlier.version == version
        ):
            reason = (
                f"the coach declined promotion {earlier.id} of {experiment}; it is not "
                "submitted again"
            )
            return SubmitOutcome(False, earlier.id, reason=reason, code="coach_declined")
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
    return _submit_acquired(ctx, sm, promotion, league_id, replaces=champion)


def _submit_acquired(
    ctx: RunContext, sm: Softmax, promotion: Promotion, league_id: str, replaces: str | None
) -> SubmitOutcome:
    """The rest of the single submit path for an acquired promotion: identity ->
    record_intent -> submit -> record_submitted -> stage the champion."""
    identity = sm.whoami()
    if identity.subject_type != "player":
        return _refuse(ctx, promotion, f"credential is a {identity.subject_type}, not a player")
    try:
        record_intent(ctx, promotion, replaces=replaces)
    except PromotionError as error:
        return SubmitOutcome(False, promotion.id, reason=str(error))
    promotion = load(ctx.canonical)
    submitted = sm.submit(promotion.platform_policy_version_id, league_id)
    if not isinstance(submitted, str):
        return _await_coach(ctx, promotion, league_id, submitted)
    record_submitted(ctx, promotion, submitted)
    stage_champion(ctx, load(ctx.canonical))
    return SubmitOutcome(True, promotion.id, submission_id=submitted)


def _waiting_fields(
    promotion: Promotion,
    refusal: PermitRequired | LeagueNotChosen,
    league_id: str,
    now: datetime.datetime,
) -> dict:
    """``waiting`` and ``waiting_detail`` after one more refusal of ``promotion``'s submit."""
    permit = isinstance(refusal, PermitRequired)
    previous = promotion.waiting_detail or {}
    refusals = int(previous.get("refusals", 0)) + 1
    ask = refusal.ask if permit else None
    # When the coach was first asked this (a new ask starts the reminder clock again).
    same = previous.get("ask") == ask and promotion.waiting == ("permit" if permit else "league")
    since = previous.get("since") if same and previous.get("since") else now.isoformat()
    detail = {
        "reason": refusal.reason,
        "ask": refusal.ask if permit else None,
        "state": refusal.state if permit else None,
        "league_id": league_id,
        "refusals": refusals,
        "next_retry_after": (now + retry_backoff(refusals)).isoformat(timespec="seconds"),
        "since": since,
    }
    return {"waiting": "permit" if permit else "league", "waiting_detail": detail}


def _await_coach(
    ctx: RunContext,
    promotion: Promotion,
    league_id: str,
    refusal: PermitRequired | LeagueNotChosen,
) -> SubmitOutcome:
    """The server refused the submit for the coach: the promotion stays at ``intent``, waiting
    (module docstring). ``reconcile`` publishes the help request and retries. The ledger push
    is best effort, as for ``record_submitted``."""
    canonical = ctx.canonical
    now = datetime.datetime.now(datetime.UTC)
    with canonical_lock(canonical):
        current = _require(load(canonical), promotion.id, "intent")
        fields = _waiting_fields(current, refusal, league_id, now)
        entry = {"stage": "intent", "at": now_iso(), "note": f"waiting: {fields['waiting']}"}
        waiting = replace(
            current, updated_at=entry["at"], history=[*current.history, entry], **fields
        )
        _save(canonical, waiting, f"promotion {promotion.id}: waiting for the coach")
    with canonical_lock(canonical):
        if not provenance.push_main(canonical, REMOTE):
            log.warning("promotion %s waits for the coach; the ledger push failed", promotion.id)
    code = "permit_required" if isinstance(refusal, PermitRequired) else "league_not_chosen"
    reason = f"the server refused the submit for the coach: {refusal.reason}"
    return SubmitOutcome(False, promotion.id, reason=reason, code=code)


def bootstrap_promotion(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    version: str,
    platform_id: str,
    league_id: str,
) -> SubmitOutcome:
    """Promote the game's starter policy as the first champion (amendment A14): the same
    C8 path as ``submit_promotion`` (acquire -> intent -> submit -> submitted -> stage; the
    merge records and publishes it; A11 recovery applies), as a ``bootstrap`` promotion with
    no experiment, evaluation outcome or decision. ``version`` is the uploaded build's version
    record (in the caller's worktree) and ``platform_id`` its platform id. The candidate is
    the champion STRATEGY at ``CHAMPION_PATH`` on the card's branch, so the merge leaves the
    champion path as it is.

    Allowed only while there is no champion and only for the shipped strategy: after taking
    the lock (one promotion in flight, so two bootstrapping cards cannot both submit), it is
    refused (aborted, ``code``) once the league shows a policy competing for our player
    (A9; ``champion_exists``), when a loop promotion already reached the league or a champion
    conflict is open (``bootstrap_blocker``, ``open_conflict``; ``champion_conflict``), and
    unless ``CHAMPION_PATH`` on the card's branch is
    byte-identical to canonical HEAD's (``not_shipped_strategy``: the one unevaluated
    promotion must not carry an edit). The caller must not hold the lock."""
    if not _take_lock(ctx, queue, "bootstrap", None, version, platform_id, CHAMPION_PATH):
        return SubmitOutcome(False, reason="another promotion holds the promotion lock")
    promotion = load(ctx.canonical)
    on_board = current_champion(ctx, sm, league_id)
    if on_board is not None:
        reason = f"the {league_id} league shows {on_board} competing for us: the champion (A9)"
        return _refuse(ctx, promotion, reason, code="champion_exists")
    blocker = bootstrap_blocker(ctx.canonical) or open_conflict(ctx, sm, league_id)
    if blocker is not None:
        return _refuse(ctx, promotion, blocker, code="champion_conflict")
    shipped = gitops.show(ctx.canonical, "HEAD", CHAMPION_PATH)
    branch = f"refs/heads/{promotion.candidate_branch}"
    if gitops.show(ctx.canonical, branch, CHAMPION_PATH) != shipped:
        reason = (
            f"{CHAMPION_PATH} on {promotion.candidate_branch} differs from the shipped one at "
            "canonical HEAD; a bootstrap promotes only the unchanged shipped strategy"
        )
        return _refuse(ctx, promotion, reason, code="not_shipped_strategy")
    return _submit_acquired(ctx, sm, promotion, league_id, replaces=None)
