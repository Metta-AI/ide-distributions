"""queue-keeper: the single canonical writer. A Hermes cron script job, every minute
(``scripts/queue_keeper.py`` -> ``python -m autoresearch.runtime.queue_keeper``).

Order of one run (Overview C1-C12; Plan 01 Task 3.9):

1. ``bootstrap`` (C1; local setup, never cards). ``--bootstrap-only`` stops here.
   Then the commit provenance check (``runtime.provenance``, C1): every commit on canonical
   HEAD's first-parent history since the last checked one must be one a runtime writer
   recorded. On a violation (a commit the runtime did not make, a rewritten history, a lost
   journal): pause (C7), one runtime ``help_request`` per violation, the spend step, and
   stop. Nothing is merged, published or admitted until an operator runs
   ``runtime accept-commits``; the run after that resumes the agent like any other pause.
   A check that raises fails closed the same way (pause, spend, stop; the step failure
   escalates after 5 runs like any other).
2. ``preflight`` (C6, A3, A5, A10), then the game's ``host_requirements`` as workers see
   them (``preflight.host_requirement_problems``: the profile ``.env`` over this job's
   environment): missing ones are logged and get one runtime ``help_request`` per distinct
   set, and the run goes on. On preflight failure, including a preflight that raises: pause
   (C7), one runtime ``help_request`` per pause (per streak for a repeatedly unavailable
   ``whoami``: ``PreflightResult.pause_key``), and stop (exit 0). No admission while paused.
   A ``skipped`` preflight (``whoami`` unavailable, fewer than 3 times in a row) stops the run
   without pausing. Both a failed and a skipped preflight still run the spend step (step 12)
   before stopping, since running cards keep running.
3. Champion check (A9): the league's champion for our player against what the loop expects
   (``promotion.league_check``: our submission's membership state, grace
   ``AUTORESEARCH_CHAMPION_GRACE_MINUTES``). On a conflict: pause (C7), one runtime
   ``help_request`` per (expected, shown) pair, and the run goes on (merges and reconcile
   still settle promotions, spend caps still apply) without resuming. A rejected submission
   gets one runtime note per promotion. Then abort a
   stale ``MERGE_HEAD`` in canonical (a kill mid ``git merge`` would wedge every later
   commit). Then foreign cards: each board task the runtime did not create is recorded once
   (``runtime.foreign``: marker and help_request), before rebuild and merges look at the
   ledger.
4. ``promotion.reconcile`` (C8, A11), then ``rebuild`` when ``needs_rebuild`` (C12).
5. ``process_handoffs`` with publication (C3, C5), then the MEMORY.md snapshot
   (``memory_snapshot``). A handoff whose request was never committed (a forged receipt) is
   never merged: one marker and one runtime ``help_request`` (``runtime.foreign``), and the
   other handoffs go on. Staged messages the publisher skipped
   (invalid envelope) are reported once each by a runtime note (marker
   ``ledger/skipped-staged/<M-id>.json``).
6. KB: when the game contract has ``source`` and the game source pin changed
   (``runtime.game_source``: fetched outside the lock), code-citation renewal
   (``kb.staleness.renew_code_citations``) and the new pin in
   ``.supervisor/ledger/game_source.json``; then stale marking against the current game
   version (``kb.staleness.mark_stale``: live claims whose ``last_checked_version`` is older
   become ``stale``; a no-op unless the game version changed), lint ``analyze`` + ``apply``, compaction (claims pending re-verification
   are kept), and the generated views; one commit. The KB maintenance commands are
   runtime-only: cards run read-only ``kb lint``/``kb hubs`` and edit only claims their refs
   name (D-23).
7. Push ``main`` and ``refs/heads/card/*`` to ``origin`` (card finish does not push), so
   merged work leaves the host early. ``main`` is pushed on its own and never forced; card
   branches are force-pushed (a retried card rebuilds its branch), and a card ref origin
   still refuses is logged once. The run ends with the same push (step 13).
8. Sweep staged skills into ``skills-proposed/`` (``proposals.sweep_skills``).
9. Route inbound (C11), before admission so new requests carry their inbound.
10. Admission (C3, C10, C11) of every committed create request (merged from cards, or written
    by the runtime) still without a final receipt. Briefings get the card's inbound, the
    planning backlog and open proposals (planning cards), and the latest signals.
    ``needs_calibration`` asks for the calibration card; ``planning_owned`` moves the request
    into the planning backlog. Then reprioritize/drop requests (``queue_ops``; each op is
    contained: a failing one is logged and retried, and after 3 failing runs given up with a
    ``failed`` marker and one runtime ``help_request``).
11. Signals (with this run's failed steps as input), then the planning request (cadence, or
    queue growth).
12. Orphaned done cards (C3), spend caps (``spend.enforce``: stop cards over their run or
    estimated-cost cap; pause when today's spend reaches the daily budget), blocked cards
    (``blocked.handle_blocked``), the daily re-raise of unanswered proposals, and resume when
    paused, preflight passed, this run's champion check found the league and the loop in
    agreement, and today's spend is under the daily budget (``spend.may_resume``).
13. Final push, then check that the remote ``main`` equals HEAD (``runtime/push_state.json``).
    After 3 consecutive failed runs, one runtime ``help_request`` per failure streak.

Fault injection (tests only): ``AUTORESEARCH_FAKE_FAULTS=crash_after=merge`` makes the process
exit at once (``os._exit``, like a kill) after a handoff's merge commit and before its
publication, so recovery from that crash can be tested on a real install.

Failure containment: each step after preflight runs in ``_step``; a step that raises is
logged to ``.supervisor/runtime/keeper.log`` and becomes a ``keeper_step_failed`` signal
input, and the run goes on with the later steps. A failed bootstrap stops the run with exit
1; everything else exits 0. A step (bootstrap included) that fails in 5 consecutive runs or
for more than an hour gets one runtime ``help_request`` per failure episode
(``escalate_step_failures``). Only one run at a time (``runtime/queue_keeper.run.lock``): a run
that finds it held exits 0 at once.

Each step takes the canonical lock itself (it is not re-entrant); network calls run outside
it. ``run`` is the whole job with injected dependencies (tests call it); ``main`` builds
them from the environment.
"""

import argparse
import datetime
import json
import os
import shutil
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch import runctx
from autoresearch.comms import runtime_messages
from autoresearch.comms.inbound import INBOX_WRITERS_ENV, inbox_writers
from autoresearch.comms.publisher import (
    STAGED_DIR,
    PublishResult,
    already_published,
    publish_hook,
    publish_message,
)
from autoresearch.games.loader import Game, load_game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.kb.apply import apply as kb_apply
from autoresearch.kb.compaction import compact
from autoresearch.kb.index import write_generated
from autoresearch.kb.lint import analyze
from autoresearch.kb.staleness import mark_stale, pending_reverification, renew_code_citations
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import read_requests
from autoresearch.queue.select import ENV_CARD_MODELS, parse_card_models, queue_from_env
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime import config_migrations, promotion, proposals, provenance, spend
from autoresearch.runtime.admission import (
    NEEDS_CALIBRATION,
    PLANNING_OWNED,
    AdmissionResult,
    admit_and_create,
    receipt_path,
)
from autoresearch.runtime.blocked import handle_blocked
from autoresearch.runtime.bootstrap import REPO_ROOT, BootstrapResult, bootstrap
from autoresearch.runtime.briefing import BriefingInputs
from autoresearch.runtime.foreign import report_foreign_cards
from autoresearch.runtime.game_source import PinUpdate, pin_update, record_pin
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of
from autoresearch.runtime.memory_snapshot import snapshot_memory
from autoresearch.runtime.merge import find_orphan_done, process_handoffs
from autoresearch.runtime.pause import is_paused, pause, pause_path, resume
from autoresearch.runtime.planning import planning_request
from autoresearch.runtime.preflight import (
    PreflightResult,
    host_requirement_problems,
    preflight,
)
from autoresearch.runtime.queue_ops import apply_ops
from autoresearch.runtime.rebuild import needs_rebuild, rebuild, request_calibration
from autoresearch.runtime.routing import (
    backlog_request,
    inbound_for_briefing,
    planning_backlog,
    route_all,
)
from autoresearch.runtime.signals import QUEUE_GROWTH, SignalConfig, compute_signals
from autoresearch.softmax_tool.fake import fault
from autoresearch.softmax_tool.protocol import Softmax
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env

LOG_FILE = "keeper.log"
RUN_LOCK = "queue_keeper.run.lock"
PUSH_STATE_FILE = "push_state.json"
FAILURES_FILE = "keeper_failures.json"
SIGNAL_LINES_FILE = "signal_lines.json"
PUSH_FAILURE_LIMIT = 3
STEP_FAILURES_FILE = "step_failures.json"
STEP_FAILURE_RUNS = 5  # consecutive failing runs of one step before a help_request ...
STEP_FAILURE_AGE = datetime.timedelta(hours=1)  # ... or this long failing, whichever first
REMOTE = "origin"
# Forced: origin's card/* copies are a one-way mirror only this runtime writes, and a retried
# card rebuilds its branch, so the remote copy is stale rather than newer.
CARD_REFSPEC = "+refs/heads/card/*:refs/heads/card/*"
GAME_ENV = "AUTORESEARCH_GAME"
DEFAULT_GAME = "gota"
# Steps that run after signals: their failures reach signals on the next run.
LATE_STEPS = ("planning", "orphans", "spend", "blocked", "proposals", "resume", "push_final")
CRASH_EXIT = 70  # exit code of an injected crash (module docstring)
CHAMPION_PAUSE = promotion.CHAMPION_PAUSE


@dataclass
class KeeperRun:
    """What one run did (for tests and the job's one-line summary)."""

    exit_code: int = 0
    skipped: bool = False  # another run held the run lock
    bootstrap: BootstrapResult | None = None
    preflight: PreflightResult | None = None
    paused: bool = False  # preflight failed and this run paused (or kept paused) the agent
    champion_checked: bool = False  # the A9 champion check ran to the end this run
    provenance_violation: provenance.Violation | None = None  # commits the runtime did not make
    champion_conflict: promotion.ChampionConflict | None = None
    foreign: list[str] = field(default_factory=list)  # foreign tasks recorded this run
    rebuilt: list[CardSpec] = field(default_factory=list)
    handoffs: list = field(default_factory=list)
    published: list[PublishResult] = field(default_factory=list)
    admitted: list[AdmissionResult] = field(default_factory=list)
    ops: list = field(default_factory=list)
    host_problems: list = field(default_factory=list)  # game host requirements workers lack
    signals: list = field(default_factory=list)
    planning: CardSpec | None = None
    orphans: list[str] = field(default_factory=list)
    spend_run: spend.SpendRun | None = None  # None: the spend step failed
    blocked: list = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    reraised: list[str] = field(default_factory=list)
    resumed: list[str] | None = None
    pushed: bool | None = None  # None: no remote (allowed) or the push step failed
    failures: dict[str, str] = field(default_factory=dict)
    escalated: list[str] = field(default_factory=list)  # steps whose help_request went out

    def summary(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "skipped": self.skipped,
            "paused": self.paused,
            "rebuilt": len(self.rebuilt),
            "handoffs": len(self.handoffs),
            "admitted": sum(1 for result in self.admitted if result.ok),
            "planning": self.planning.request_id if self.planning else None,
            "pushed": self.pushed,
            "failures": self.failures,
        }


# --- logging and small state files (disposable, in runtime/) ---


def _log(ctx: RunContext, message: str) -> None:
    path = runtime_dir(ctx) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _read_state(ctx: RunContext, name: str, default):
    path = runtime_dir(ctx) / name
    try:
        return read_json(path) if path.exists() else default
    except ValueError:
        return default  # disposable: a damaged file starts over


INBOX_WRITERS_STEP = "inbox_writers"


def _routing_step(
    run: KeeperRun, ctx: RunContext, queue: Queue, env: Mapping[str, str], allow_no_remote: bool
):
    """Route inbound (C11). Direct inbox writes are adopted only from the users in
    ``AUTORESEARCH_INBOX_WRITERS`` when it is set. When it names an unknown user, adoption is
    skipped (direct writes wait, untouched and unanswered, rather than being rejected for
    good), the failure is recorded as the step ``inbox_writers`` so it escalates like any
    failing step, and committed inbound messages are still routed. When it is unset, a run
    that took direct writes logs that they are unauthenticated."""
    adopt = True
    try:
        writers = inbox_writers(env.get(INBOX_WRITERS_ENV))
    except ValueError as error:
        adopt, writers = False, None
        run.failures[INBOX_WRITERS_STEP] = f"ValueError: {error}"
        _log(ctx, f"step {INBOX_WRITERS_STEP} failed: {error}; direct inbox writes are not adopted")
    actions = route_all(
        ctx, queue, allow_no_remote=allow_no_remote, inbox_writers=writers, adopt=adopt
    )
    if adopt and writers is None and any(a.kind in ("adopted", "write_rejected") for a in actions):
        _log(
            ctx,
            f"warning: direct inbox writes are unauthenticated: {INBOX_WRITERS_ENV} is unset, "
            "so any process running as the workspace user can write inbound messages",
        )
    for action in actions:
        if action.kind == "unreadable":
            _log(ctx, f"warning: committed inbox entry {action.inbound_id} is not a JSON object")
    return actions


def _step(run: KeeperRun, ctx: RunContext, name: str, fn: Callable, *args, **kwargs):
    """Run one step; contain and record its failure (module docstring)."""
    try:
        return fn(*args, **kwargs)
    except Exception as error:  # noqa: BLE001 - containment is the point
        message = f"{type(error).__name__}: {error}"
        run.failures[name] = message
        _log(ctx, f"step {name} failed: {message}\n{traceback.format_exc()}")
        return None


# --- pause ---


def pause_for_preflight(
    ctx: RunContext, queue: Queue, result: PreflightResult, allow_no_remote: bool
) -> None:
    """Pause (C7; idempotent) and publish one help_request for this pause (C6)."""
    canonical = ctx.canonical
    reasons = "; ".join(result.reasons)
    pause(queue, ledger_of(canonical), f"preflight failed: {reasons}")
    with canonical_lock(canonical):
        paused_at = read_json(pause_path(ledger_of(canonical)))["paused_at"]
        message = runtime_messages.agent_paused(reasons, paused_at, key=result.pause_key)
        if already_published(canonical, message):
            return
        publish_message(
            canonical, message, allow_no_remote, priority="P0", expects_response=True
        )


def check_champion(
    run: KeeperRun,
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    game: Game,
    now: datetime.datetime,
    env: Mapping[str, str],
    allow_no_remote: bool,
) -> None:
    """A9: compare the league's champion with what the loop expects
    (``promotion.league_check``, which also tracks our submission's membership); on a conflict
    pause and publish one help_request per (expected, shown) pair. When the league did not take
    our submission (rejected, withdrawn, disqualified), tell the user once per promotion: a
    real outcome, not a conflict. ``run.champion_checked`` is set only when the check
    completes, so a failed league read never lets the agent resume."""
    canonical = ctx.canonical
    check = promotion.league_check(
        ctx, sm, game.league_id, now, promotion.champion_grace(env), record=True
    )
    conflict = run.champion_conflict = check.conflict
    if check.submission is not None and check.submission.stage == "unknown":
        detail = check.submission.detail or "not listed"
        _log(ctx, f"champion check: our submission's league state is unknown ({detail})")
    if check.submission is not None and check.submission.stage == "rejected":
        rejected = promotion.rejection_message(check.basis, game.league_id, check.submission.detail)
        with canonical_lock(canonical):
            if not already_published(canonical, rejected):
                publish_message(canonical, rejected, allow_no_remote)
    if conflict is not None:
        _log(ctx, f"champion conflict: {conflict.detail}")
        pause(queue, ledger_of(canonical), f"{CHAMPION_PAUSE}: {conflict.detail}")
        message = runtime_messages.champion_conflict(
            game.league_id, conflict.expected, conflict.shown, conflict.waited_out
        )
        with canonical_lock(canonical):
            if not already_published(canonical, message):
                publish_message(
                    canonical, message, allow_no_remote, priority="P0", expects_response=True
                )
    run.champion_checked = True


def check_provenance(
    run: KeeperRun, ctx: RunContext, queue: Queue, now: datetime.datetime, allow_no_remote: bool
) -> None:
    """C1: check that every new commit on canonical is one the runtime made
    (``provenance.check``); on a violation pause and publish one help_request for it. Each
    ``accept-commits`` anchor gets one note, so an accept (even a forged one) is visible."""
    canonical = ctx.canonical
    with canonical_lock(canonical):
        for sha in provenance.recover(canonical):
            _log(ctx, f"provenance: recorded and settled {sha}, left unfinished by a crash")
        violation = run.provenance_violation = provenance.check(canonical)
        invalid = provenance.invalid_lines(canonical)
        for anchor in provenance.operator_anchors(canonical):
            try:
                info = provenance.describe(canonical, anchor["sha"])
                author, subject = info.author, info.subject
            except gitops.GitError:  # the accepted commit is gone from the repository
                author, subject = "an unknown author", "a commit no longer in the workspace"
            note = runtime_messages.commits_accepted(anchor["sha"], anchor["at"], author, subject)
            if not already_published(canonical, note):
                publish_message(canonical, note, allow_no_remote)
    if invalid:
        _log(ctx, f"provenance: {invalid} journal line(s) do not verify and are ignored")
    if violation is None:
        return
    _log(ctx, f"provenance violation: {json.dumps(violation.to_dict(), sort_keys=True)}")
    pause(queue, ledger_of(canonical), f"{provenance.PAUSE_REASON}: {violation.summary()}")
    message = runtime_messages.commit_provenance(violation.to_dict(), now.date().isoformat())
    with canonical_lock(canonical):
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote)


def resume_after_preflight(ctx: RunContext, queue: Queue, allow_no_remote: bool) -> list[str]:
    canonical = ctx.canonical
    reason = read_json(pause_path(ledger_of(canonical))).get("reason", "")
    resumed = resume(queue, ledger_of(canonical))
    message = runtime_messages.agent_resumed(
        len(resumed),
        reason.startswith(CHAMPION_PAUSE),
        provenance=reason.startswith(provenance.PAUSE_REASON),
    )
    with canonical_lock(canonical):
        publish_message(canonical, message, allow_no_remote)
    return resumed


# --- steps ---


def abort_stale_merge(canonical: Path) -> bool:
    """Abort a merge left in progress (MERGE_HEAD) by a killed run. True if it did."""
    with canonical_lock(canonical):
        try:
            gitops.git(canonical, "rev-parse", "-q", "--verify", "MERGE_HEAD")
        except gitops.GitError:
            return False
        gitops.git(canonical, "merge", "--abort")
        return True


def _staged_title(canonical: Path, message_id: str) -> str | None:
    """The title of a staged message the publisher skipped, if it has a readable one."""
    content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
    try:
        message = json.loads(content) if content is not None else None
    except ValueError:
        return None
    title = message.get("title") if isinstance(message, dict) else None
    return title if isinstance(title, str) and title.strip() else None


def report_skipped(canonical: Path, results: list[PublishResult], allow_no_remote: bool) -> None:
    """One runtime note per staged message the publisher skipped (invalid envelope)."""
    skipped: dict[str, list[str]] = {}
    for result in results:
        skipped.update(result.skipped)
    if not skipped:
        return
    with canonical_lock(canonical):
        for message_id, problems in sorted(skipped.items()):
            marker = ledger_of(canonical) / "skipped-staged" / f"{message_id}.json"
            if committed_json(canonical, marker) is not None:
                continue
            message = runtime_messages.staged_not_published(
                message_id, _staged_title(canonical, message_id), problems
            )
            if not already_published(canonical, message):
                publish_message(canonical, message, allow_no_remote)
            write_json(marker, {"id": message_id, "problems": problems})
            commit(canonical, [marker], f"publish: skipped staged {message_id} reported")


def kb_step(
    canonical: Path,
    now: datetime.datetime,
    game_version: str | None = None,
    pin: PinUpdate | None = None,
) -> None:
    """Code-citation renewal (when the game source pin changed), stale marking (when the game
    version is known), lint apply, compaction and generated views (System Design §3.5); one
    commit, which also records a new game source pin.

    A renewal failure does not stop the rest: the pin stays unrecorded (the next run retries),
    the other KB work is committed, and the error is raised afterwards so the step is logged
    as failed and escalates. ``cards/requests`` is committed as a directory, so
    re-verification requests a failed earlier renewal left uncommitted are committed too."""
    layout = Layout(canonical)
    version = str(game_version).strip() if game_version else ""
    renewal_error: Exception | None = None
    with canonical_lock(canonical):
        extra: list[Path] = []
        if pin is not None and (pin.old is None or version):
            try:
                if pin.old is not None and pin.checkout is not None:
                    renew_code_citations(
                        layout, pin.checkout, pin.old, pin.new, current_version=version, now=now
                    )
                extra.append(record_pin(canonical, pin, now))
            except Exception as error:  # noqa: BLE001 - re-raised after the commit below
                renewal_error = error
        if version:
            mark_stale(layout, version, now)
        kb_apply(layout, analyze(layout), now)
        compact(layout, now, pending_reverify=pending_reverification(layout))
        write_generated(layout)
        if layout.card_requests.exists():
            extra.append(layout.card_requests)
        commit(canonical, [layout.kb, *extra], "kb: lint fixes, compaction, generated views")
    if renewal_error is not None:  # _step logs it and counts the kb step as failed
        raise RuntimeError(f"code-citation renewal failed: {renewal_error}") from renewal_error


def _remote_main(canonical: Path) -> str | None:
    try:
        output = gitops.git(canonical, "ls-remote", REMOTE, "refs/heads/main")
    except gitops.GitError:
        return None
    return output.split()[0] if output else None


def _push_main_and_cards(canonical: Path) -> list[str]:
    """Push main, then the card branches separately so a card refusal never touches main's
    result. Returns the card refs origin rejected."""
    provenance.push_main(canonical, REMOTE)
    _, rejected = gitops.push_with_rejections(canonical, REMOTE, [CARD_REFSPEC])
    return rejected


def push_early(canonical: Path) -> None:
    """Best-effort push after merges (step 7); the final push records the outcome."""
    if REMOTE in gitops.git(canonical, "remote").split():
        with canonical_lock(canonical):
            _push_main_and_cards(canonical)


def push_step(ctx: RunContext, allow_no_remote: bool) -> bool | None:
    """Push and record whether the remote main equals HEAD (module docstring, step 13)."""
    canonical = ctx.canonical
    if REMOTE not in gitops.git(canonical, "remote").split():
        if allow_no_remote:
            return None
        raise RuntimeError(f"canonical {canonical} has no {REMOTE!r} remote")
    with canonical_lock(canonical):
        rejected_cards = _push_main_and_cards(canonical)
        head = gitops.head(canonical)
    remote_head = _remote_main(canonical)
    ok = remote_head == head
    state = _read_state(ctx, PUSH_STATE_FILE, {})
    failures = 0 if ok else int(state.get("consecutive_failures", 0)) + 1
    help_sent = bool(state.get("help_sent")) and not ok
    for ref in sorted(set(rejected_cards) - set(state.get("rejected_cards", []))):
        _log(ctx, f"push: card branch rejected by origin: {ref}")
    if failures >= PUSH_FAILURE_LIMIT and not help_sent:
        with canonical_lock(canonical):
            message = runtime_messages.push_failing(REMOTE, failures, head, remote_head)
            publish_message(canonical, message, allow_no_remote=True)
        help_sent = True
    write_json(
        runtime_dir(ctx) / PUSH_STATE_FILE,
        {
            "ok": ok,
            "head": head,
            "remote_head": remote_head,
            "consecutive_failures": failures,
            "help_sent": help_sent,
            "rejected_cards": sorted(rejected_cards),
            "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        },
    )
    return ok


def _publish_step_help(ctx: RunContext, step: str, entry: dict, allow_no_remote: bool) -> None:
    canonical = ctx.canonical
    message = runtime_messages.step_failing(
        step, entry["runs"], entry["since"], entry["error"], f".supervisor/runtime/{LOG_FILE}"
    )
    with canonical_lock(canonical):
        if already_published(canonical, message):
            return
        publish_message(
            canonical, message, allow_no_remote, priority="P1", expects_response=True
        )


def escalate_step_failures(
    ctx: RunContext,
    failures: Mapping[str, str],
    now: datetime.datetime,
    allow_no_remote: bool,
    *,
    all_steps_ran: bool,
) -> list[str]:
    """One runtime ``help_request`` per failure episode of a step that keeps failing.

    Signal escalation counts handoff iterations, so a step that fails every run (and may be
    the reason no handoffs arrive) would never escalate there. This counts runs instead, in
    ``runtime/step_failures.json``: ``{step: {since, runs, error, help_sent}}``. A step that
    fails in ``STEP_FAILURE_RUNS`` consecutive runs, or has been failing for more than
    ``STEP_FAILURE_AGE``, gets one help_request for the episode (title names the step and
    ``since``; ``help_sent`` and the outbox dedupe it). A run in which the step does not fail
    ends the episode; steps after preflight only count as not failing when the run got
    through all of them (not on a paused run). Bootstrap failures count too; their
    help_request goes out once canonical exists to publish from. Returns the steps escalated.
    """
    state = _read_state(ctx, STEP_FAILURES_FILE, {})
    kept = {step: entry for step, entry in state.items() if step in failures}
    if not all_steps_ran:  # unknown whether the steps that did not run would have failed
        kept |= {step: entry for step, entry in state.items() if step != "bootstrap"}
    escalated = []
    for step, error in sorted(failures.items()):
        entry = dict(kept.get(step) or {"since": now.isoformat(), "runs": 0, "help_sent": False})
        entry["runs"] = int(entry["runs"]) + 1
        entry["error"] = error
        since = datetime.datetime.fromisoformat(entry["since"])
        due = entry["runs"] >= STEP_FAILURE_RUNS or now - since > STEP_FAILURE_AGE
        if due and not entry["help_sent"] and (ctx.canonical / ".git").exists():
            try:
                _publish_step_help(ctx, step, entry, allow_no_remote)
            except Exception as problem:  # noqa: BLE001 - retried on the next run
                _log(ctx, f"could not report failing step {step}: {problem}")
            else:
                entry["help_sent"] = True
                escalated.append(step)
        kept[step] = entry
    write_json(runtime_dir(ctx) / STEP_FAILURES_FILE, kept)
    return escalated


def _briefing_inputs(
    canonical: Path, spec: CardSpec, signal_lines: list[str], now: datetime.datetime
) -> Callable[[str], BriefingInputs]:
    planning = spec.mode == "planning"

    def inputs(task_id: str) -> BriefingInputs:
        return BriefingInputs(
            signals=signal_lines,
            inbound=inbound_for_briefing(canonical, task_id, spec.request_id, planning),
            open_proposals=proposals.open_proposals(canonical, now) if planning else [],
            planning_backlog=planning_backlog(canonical, task_id) if planning else [],
        )

    return inputs


def _backlog_rejected_planning(canonical: Path, spec: CardSpec, receipt: dict) -> None:
    """C11: a planning request rejected because planning is owned joins the backlog."""
    path = ledger_of(canonical) / "backlog" / f"{spec.request_id}.json"
    with canonical_lock(canonical):
        if committed_json(canonical, path) is not None:
            return
        backlog_request(canonical, spec, receipt.get("reason", PLANNING_OWNED))
        commit(canonical, [path], f"backlog: planning request {spec.request_id}")


def admission_step(
    run: KeeperRun,
    ctx: RunContext,
    queue: Queue,
    game: Game,
    game_version: str | None,
    profile: str,
    now: datetime.datetime,
    allow_no_remote: bool = False,
) -> None:
    """Admit committed create requests (module docstring, step 10); one failing request does
    not stop the others. Then the reprioritize/drop requests, each contained on its own
    (``queue_ops``: a failing op is retried, then given up with one help_request)."""
    canonical = ctx.canonical
    signal_lines = _read_state(ctx, SIGNAL_LINES_FILE, [])
    needs_calibration = False
    for request in read_requests(canonical, "HEAD"):
        if request.get("op") != "create":
            continue
        spec = CardSpec(**request["spec"])
        receipt = committed_json(canonical, receipt_path(ledger_of(canonical), spec.request_id))
        if receipt is not None and receipt["state"] == "rejected":
            if spec.mode == "planning" and PLANNING_OWNED in receipt.get("reason", ""):
                _step(
                    run,
                    ctx,
                    f"backlog {spec.request_id}",
                    _backlog_rejected_planning,
                    canonical,
                    spec,
                    receipt,
                )
            continue
        if receipt is not None and receipt["state"] != "admitted":
            continue
        inputs = _briefing_inputs(canonical, spec, signal_lines, now)
        result = _step(
            run,
            ctx,
            f"admission {spec.request_id}",
            admit_and_create,
            ctx,
            queue,
            spec,
            profile,
            inputs,
            game,
            game_version,
        )
        if result is None:
            continue
        run.admitted.append(result)
        needs_calibration |= NEEDS_CALIBRATION in result.reasons
        if PLANNING_OWNED in result.reasons:
            receipt = committed_json(canonical, receipt_path(ledger_of(canonical), spec.request_id))
            _step(
                run,
                ctx,
                f"backlog {spec.request_id}",
                _backlog_rejected_planning,
                canonical,
                spec,
                receipt,
            )
    if needs_calibration and game_version:
        _step(run, ctx, "calibration", request_calibration, ctx, game_version)
    run.ops = _step(run, ctx, "ops", apply_ops, ctx, queue, allow_no_remote) or []
    for outcome in run.ops:
        if outcome.state in ("retrying", "failed"):
            _log(ctx, f"ops: {outcome.op} {outcome.request_id} {outcome.state}: {outcome.detail}")


def _spend_step(
    run: KeeperRun, ctx: RunContext, queue: Queue, config: spend.SpendConfig,
    now: datetime.datetime, allow_no_remote: bool,
) -> spend.SpendRun:
    result = spend.enforce(ctx, queue, config, now, allow_no_remote)
    for task_id, why in result.stopped:
        _log(ctx, f"spend: stopped card {task_id}: {why}")
    for key, error in result.failures.items():
        if key == spend.DB_FAILURE:
            run.failures[key] = error
            _log(ctx, f"spend: cannot read state.db: {error}")
        else:
            run.failures[f"spend {key}"] = error
            _log(ctx, f"spend: {key}: {error}")
    for task_id, error in result.stop_failures.items():  # retried next run: log only
        _log(ctx, f"spend: could not stop card {task_id} (retrying next run): {error}")
    if result.paused:
        _log(ctx, f"spend: paused, today's estimated spend is ${result.day_usd:.2f}")
    new = spend.new_unpriced(ctx, result.unpriced)
    if new:
        _log(
            ctx,
            f"spend: warning: Hermes has no cost estimate for session(s) {', '.join(new)}; "
            f"priced from token counts with {spend.ENV_FALLBACK_PRICES}",
        )
    return result


def _spend_steps(
    run: KeeperRun, ctx: RunContext, queue: Queue, env: Mapping[str, str],
    now: datetime.datetime, allow_no_remote: bool,
) -> spend.SpendConfig | None:
    """The spend step (``spend.enforce``); returns its config for the blocked step, or None
    when the config is invalid (the step failure is recorded)."""
    config = _step(run, ctx, "spend", spend.SpendConfig.from_env, env)
    if config is not None:
        run.spend_run = _step(
            run, ctx, "spend", _spend_step, run, ctx, queue, config, now, allow_no_remote
        )
    return config


def _card_models_check(
    run: KeeperRun, ctx: RunContext, env: Mapping[str, str], allow_no_remote: bool
) -> None:
    """Report entries of ``AUTORESEARCH_CARD_MODELS`` that were skipped (the queue uses the
    default model for them): a log line every run, the step failure ``card_models`` (so it
    escalates and shows as a signal), and one help_request per distinct value."""
    raw = env.get(ENV_CARD_MODELS) or ""
    _models, problems = parse_card_models(raw)
    if not problems:
        return
    text = "; ".join(problems)
    run.failures["card_models"] = text
    _log(ctx, f"{ENV_CARD_MODELS}: {text}; those modes use the default model")
    if not (ctx.canonical / ".git").exists():
        return
    message = runtime_messages.card_models_invalid(raw, problems)
    with canonical_lock(ctx.canonical):
        if not already_published(ctx.canonical, message):
            publish_message(ctx.canonical, message, allow_no_remote, expects_response=True)


def _host_requirements_check(
    ctx: RunContext, game: Game, env: Mapping[str, str], allow_no_remote: bool
) -> list[str]:
    """The game's ``host_requirements`` that workers lack (``preflight``): a log line every
    run and one help_request per distinct set. A warning, not a pause or a step failure: a
    missing build tool stops builds, not the rest of the agent's work."""
    problems = host_requirement_problems(game.host_env, game.host_tools, env, ctx.hermes_home)
    if not problems:
        return []
    _log(ctx, f"host requirements of {game.name} missing: {'; '.join(problems)}")
    if not (ctx.canonical / ".git").exists():
        return problems
    message = runtime_messages.host_requirements_missing(game.name, problems)
    with canonical_lock(ctx.canonical):
        if not already_published(ctx.canonical, message):
            publish_message(ctx.canonical, message, allow_no_remote, expects_response=True)
    return problems


def _rebuild_step(ctx, queue, sm, game_version, now, allow_no_remote) -> list[CardSpec]:
    if not needs_rebuild(ctx, queue):
        return []
    if not game_version:
        raise RuntimeError("the board needs a rebuild but the game version is unknown")
    return rebuild(
        ctx, queue, sm, game_version=game_version, now=now, allow_no_remote=allow_no_remote
    )


def _signals_step(run, ctx, queue, now, cfg, pre, allow_no_remote) -> list:
    earlier = _read_state(ctx, FAILURES_FILE, {})
    failures = {k: v for k, v in earlier.items() if k in LATE_STEPS} | run.failures
    signals = compute_signals(
        ctx,
        queue,
        now,
        cfg,
        preflight=pre,
        allow_no_remote=allow_no_remote,
        step_failures=failures,
    )
    write_json(runtime_dir(ctx) / SIGNAL_LINES_FILE, [signal.line() for signal in signals])
    return signals


def _planning_step(ctx, queue, now, signals) -> CardSpec | None:
    growth = any(signal.code == QUEUE_GROWTH for signal in signals)
    return planning_request(ctx, queue, now) or (
        planning_request(ctx, queue, now, growth=True) if growth else None
    )


# --- the job ---


def _run_steps(
    run: KeeperRun,
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    game: Game,
    now: datetime.datetime,
    *,
    env: Mapping[str, str],
    profile: str,
    allow_no_remote: bool,
    preflight_kwargs: dict,
    signal_config: SignalConfig,
) -> None:
    canonical = ctx.canonical
    if run.bootstrap is not None and run.bootstrap.provenance_error:
        # Bootstrap could not read the journal: the check would fail the same way.
        run.failures["provenance"] = f"bootstrap: {run.bootstrap.provenance_error}"
    else:
        _step(run, ctx, "provenance", check_provenance, run, ctx, queue, now, allow_no_remote)
    if "provenance" in run.failures:
        # Fail closed: a check that cannot run is treated like a violation (no merge, publish
        # or admission, and no resume). The step failure escalates like any failing step.
        error = run.failures["provenance"]
        reason = f"{provenance.PAUSE_REASON}: the check could not run: {error}"
        _step(run, ctx, "provenance_pause", pause, queue, ledger_of(canonical), reason)
    if run.provenance_violation is not None or "provenance" in run.failures:
        run.paused = True
        # Running cards keep running while paused: the caps still apply to them.
        _spend_steps(run, ctx, queue, env, now, allow_no_remote)
        return
    try:
        run.preflight = preflight(env, sm, canonical, now, **preflight_kwargs)
    except Exception as error:  # noqa: BLE001 - a preflight that cannot finish has failed
        _log(ctx, f"preflight raised: {error}\n{traceback.format_exc()}")
        reason = f"preflight raised: {type(error).__name__}: {error}"
        run.preflight = PreflightResult(ok=False, reasons=[reason])
    for warning in run.preflight.warnings:
        _log(ctx, f"preflight warning: {warning}")
    _card_models_check(run, ctx, env, allow_no_remote)
    run.host_problems = (
        _step(run, ctx, "host", _host_requirements_check, ctx, game, env, allow_no_remote) or []
    )
    if run.preflight.skipped:
        _log(ctx, "preflight skipped this run: whoami unavailable")
        # The spend caps read only the local state.db and the board, not Softmax: cards keep
        # running through a whoami blip, so their caps (and the daily budget) still apply.
        _spend_steps(run, ctx, queue, env, now, allow_no_remote)
        return
    if not run.preflight.ok:
        run.paused = True
        _log(ctx, f"preflight failed: {run.preflight.reasons}")
        _step(run, ctx, "pause", pause_for_preflight, ctx, queue, run.preflight, allow_no_remote)
        # Running cards keep running while paused (low credit, expired credential): the caps
        # still apply to them.
        _spend_steps(run, ctx, queue, env, now, allow_no_remote)
        return

    _step(
        run, ctx, "champion", check_champion, run, ctx, queue, sm, game, now, env, allow_no_remote
    )
    _step(run, ctx, "merge_head", abort_stale_merge, canonical)
    run.foreign = (
        _step(run, ctx, "foreign", report_foreign_cards, ctx, queue, allow_no_remote) or []
    )
    game_version = _step(run, ctx, "game_version", sm.game_version, game.league_id)
    game_version = str(game_version).strip() if game_version is not None else None
    _step(run, ctx, "promotion", promotion.reconcile, ctx, queue, sm, game.league_id)
    run.rebuilt = (
        _step(
            run, ctx, "rebuild", _rebuild_step, ctx, queue, sm, game_version, now, allow_no_remote
        )
        or []
    )

    def publish(target: Path, merge_sha: str, handoff: dict) -> None:
        if fault(env, "crash_after") == "merge":
            os._exit(CRASH_EXIT)  # the injected crash: no cleanup, like a kill
        run.published.append(publish_hook(target, merge_sha, handoff, allow_no_remote))

    store = RecordStore(Layout(canonical))
    run.handoffs = (
        _step(
            run,
            ctx,
            "handoffs",
            process_handoffs,
            ctx,
            queue,
            store,
            publish=publish,
            allow_no_remote=allow_no_remote,
        )
        or []
    )
    _step(run, ctx, "memory_snapshot", snapshot_memory, ctx)
    _step(run, ctx, "skipped_staged", report_skipped, canonical, run.published, allow_no_remote)
    pin = _step(run, ctx, "game_source", pin_update, ctx, sm, game)
    _step(run, ctx, "kb", kb_step, canonical, now, game_version, pin)
    _step(run, ctx, "push", push_early, canonical)
    run.skills = _step(run, ctx, "skills", proposals.sweep_skills, ctx, allow_no_remote) or []
    _step(run, ctx, "routing", _routing_step, run, ctx, queue, env, allow_no_remote)
    _step(
        run, ctx, "admission", admission_step,
        run, ctx, queue, game, game_version, profile, now, allow_no_remote,
    )  # fmt: skip
    run.signals = (
        _step(
            run,
            ctx,
            "signals",
            _signals_step,
            run,
            ctx,
            queue,
            now,
            signal_config,
            run.preflight,
            allow_no_remote,
        )
        or []
    )
    run.planning = _step(run, ctx, "planning", _planning_step, ctx, queue, now, run.signals)
    run.orphans = _step(run, ctx, "orphans", find_orphan_done, queue, ledger_of(canonical)) or []
    spend_config = _spend_steps(run, ctx, queue, env, now, allow_no_remote)
    run.blocked = (
        _step(run, ctx, "blocked", handle_blocked, ctx, queue, allow_no_remote, spend_config)
        or []
    )
    run.reraised = _step(run, ctx, "proposals", proposals.reraise, ctx, now, allow_no_remote) or []
    if (
        is_paused(ledger_of(canonical))
        and run.champion_checked
        and not run.champion_conflict
        and spend.may_resume(ctx, run.spend_run, now)
    ):
        run.resumed = _step(run, ctx, "resume", resume_after_preflight, ctx, queue, allow_no_remote)
    run.pushed = _step(run, ctx, "push_final", push_step, ctx, allow_no_remote)


def run(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    game: Game,
    now: datetime.datetime,
    *,
    env: Mapping[str, str],
    profile: str | None = None,
    bootstrap_only: bool = False,
    allow_no_remote: bool = False,
    runner: config_migrations.Runner = config_migrations.hermes_runner,
    repo_root: Path = REPO_ROOT,
    disk_free: Callable = shutil.disk_usage,
    http_get: Callable | None = None,
    signal_config: SignalConfig | None = None,
) -> KeeperRun:
    """One queue-keeper run (module docstring). ``profile`` (default: the profile home's
    name) is the assignee of admitted cards; ``allow_no_remote`` lets tests run without an
    ``origin``; ``runner``, ``disk_free`` and ``http_get`` are the bootstrap and preflight
    seams."""
    result = KeeperRun()
    try:
        with file_lock(runtime_dir(ctx) / RUN_LOCK, timeout_s=0):
            result.bootstrap = bootstrap(ctx, env, queue, runner=runner, repo_root=repo_root)
            for message in result.bootstrap.actions + result.bootstrap.warnings:
                _log(ctx, f"bootstrap: {message}")
            if not result.bootstrap.ok:
                _log(ctx, f"bootstrap failed: {result.bootstrap.error}")
                result.exit_code = 1
                result.failures["bootstrap"] = result.bootstrap.error or "unknown error"
                result.escalated = escalate_step_failures(
                    ctx, result.failures, now, allow_no_remote, all_steps_ran=False
                )
                return result
            if bootstrap_only:
                return result
            preflight_kwargs: dict = {"disk_free": disk_free, "hermes_home": ctx.hermes_home}
            if http_get is not None:
                preflight_kwargs["http_get"] = http_get
            _run_steps(
                result,
                ctx,
                queue,
                sm,
                game,
                now,
                env=env,
                profile=profile or ctx.hermes_home.name,
                allow_no_remote=allow_no_remote,
                preflight_kwargs=preflight_kwargs,
                signal_config=signal_config or SignalConfig(),
            )
            write_json(runtime_dir(ctx) / FAILURES_FILE, result.failures)
            result.escalated = escalate_step_failures(
                ctx, result.failures, now, allow_no_remote, all_steps_ran=not result.paused
            )
    except LockTimeout:
        result.skipped = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="queue-keeper", description=__doc__.splitlines()[0])
    parser.add_argument("--bootstrap-only", action="store_true", help="stop after bootstrap")
    args = parser.parse_args(argv)
    env = os.environ
    try:
        ctx = runctx.from_env(env)
        queue = queue_from_env(env, ctx)
        game = load_game(REPO_ROOT, env.get(GAME_ENV) or DEFAULT_GAME)
        sm = softmax_from_env(env, ctx.canonical)
    except Exception as error:  # noqa: BLE001 - report any setup failure and fail the job
        print(f"queue-keeper: cannot start: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    now = datetime.datetime.now(datetime.UTC)
    result = run(
        ctx,
        queue,
        sm,
        game,
        now,
        env=env,
        bootstrap_only=args.bootstrap_only,
        runner=config_migrations.hermes_runner,
    )
    print(json.dumps(result.summary(), sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
