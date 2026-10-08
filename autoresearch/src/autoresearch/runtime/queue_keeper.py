"""queue-keeper: the single canonical writer. A Hermes cron script job, every minute
(``scripts/queue_keeper.py`` -> ``python -m autoresearch.runtime.queue_keeper``).

Order of one run (Overview C1-C12; Plan 01 Task 3.9):

1. ``bootstrap`` (C1; local setup, never cards). ``--bootstrap-only`` stops here.
2. ``preflight`` (C6, A3, A5, A10). On failure: pause (C7), one runtime ``help_request`` per
   pause, and stop (exit 0). No admission while paused.
3. Abort a stale ``MERGE_HEAD`` in canonical (a kill mid ``git merge`` would wedge every later
   commit).
4. ``promotion.reconcile`` (C8, A11), then ``rebuild`` when ``needs_rebuild`` (C12).
5. ``process_handoffs`` with publication (C3, C5). Staged messages the publisher skipped
   (invalid envelope) are reported once each by a runtime note (marker
   ``ledger/skipped-staged/<M-id>.json``).
6. KB: lint ``analyze`` + ``apply``, compaction (claims pending re-verification are kept), and
   the generated views; one commit.
7. Push ``main`` and ``refs/heads/card/*`` to ``origin`` (card finish does not push), so
   merged work leaves the host early. The run ends with the same push (step 13).
8. Sweep staged skills into ``skills-proposed/`` (``proposals.sweep_skills``).
9. Route inbound (C11), before admission so new requests carry their inbound.
10. Admission (C3, C10, C11) of every committed create request (merged from cards, or written
    by the runtime) still without a final receipt. Briefings get the card's inbound, the
    planning backlog and open proposals (planning cards), and the latest signals.
    ``needs_calibration`` asks for the calibration card; ``planning_owned`` moves the request
    into the planning backlog. Then reprioritize/drop requests (``queue_ops``).
11. Signals (with this run's failed steps as input), then the planning request (cadence, or
    queue growth).
12. Orphaned done cards (C3), blocked cards (``blocked.handle_blocked``), the daily re-raise
    of unanswered proposals, and resume when paused and preflight passed.
13. Final push, then check that the remote ``main`` equals HEAD (``runtime/push_state.json``).
    After 3 consecutive failed runs, one runtime ``help_request`` per failure streak.

Failure containment: each step after preflight runs in ``_step``; a step that raises is
logged to ``.supervisor/runtime/keeper.log`` and becomes a ``keeper_step_failed`` signal
input, and the run goes on with the later steps. A failed bootstrap stops the run with exit
1; everything else exits 0. Only one run at a time (``runtime/queue_keeper.run.lock``): a run
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
from autoresearch.comms.publisher import OUTBOX_DIR, PublishResult, publish_hook, publish_runtime
from autoresearch.games.loader import Game, load_game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.kb.apply import apply as kb_apply
from autoresearch.kb.compaction import compact
from autoresearch.kb.index import write_generated
from autoresearch.kb.lint import analyze
from autoresearch.kb.staleness import pending_reverification
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import read_requests
from autoresearch.queue.select import queue_from_env
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime import config_migrations, promotion, proposals
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
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of
from autoresearch.runtime.merge import find_orphan_done, process_handoffs
from autoresearch.runtime.pause import is_paused, pause, pause_path, resume
from autoresearch.runtime.planning import planning_request
from autoresearch.runtime.preflight import PreflightResult, preflight
from autoresearch.runtime.queue_ops import apply_ops
from autoresearch.runtime.rebuild import needs_rebuild, rebuild, request_calibration
from autoresearch.runtime.routing import (
    backlog_request,
    inbound_for_briefing,
    planning_backlog,
    route_all,
)
from autoresearch.runtime.signals import QUEUE_GROWTH, SignalConfig, compute_signals
from autoresearch.softmax_tool.protocol import Softmax
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env

LOG_FILE = "keeper.log"
RUN_LOCK = "queue_keeper.run.lock"
PUSH_STATE_FILE = "push_state.json"
FAILURES_FILE = "keeper_failures.json"
SIGNAL_LINES_FILE = "signal_lines.json"
PUSH_FAILURE_LIMIT = 3
REMOTE = "origin"
PUSH_REFSPECS = ["HEAD:refs/heads/main", "refs/heads/card/*:refs/heads/card/*"]
GAME_ENV = "AUTORESEARCH_GAME"
DEFAULT_GAME = "gota"
# Steps that run after signals: their failures reach signals on the next run.
LATE_STEPS = ("planning", "orphans", "blocked", "proposals", "resume", "push_final")


@dataclass
class KeeperRun:
    """What one run did (for tests and the job's one-line summary)."""

    exit_code: int = 0
    skipped: bool = False  # another run held the run lock
    bootstrap: BootstrapResult | None = None
    preflight: PreflightResult | None = None
    paused: bool = False  # preflight failed and this run paused (or kept paused) the agent
    rebuilt: list[CardSpec] = field(default_factory=list)
    handoffs: list = field(default_factory=list)
    published: list[PublishResult] = field(default_factory=list)
    admitted: list[AdmissionResult] = field(default_factory=list)
    ops: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    planning: CardSpec | None = None
    orphans: list[str] = field(default_factory=list)
    blocked: list = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    reraised: list[str] = field(default_factory=list)
    resumed: list[str] | None = None
    pushed: bool | None = None  # None: no remote (allowed) or the push step failed
    failures: dict[str, str] = field(default_factory=dict)

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


def _step(run: KeeperRun, ctx: RunContext, name: str, fn: Callable, *args, **kwargs):
    """Run one step; contain and record its failure (module docstring)."""
    try:
        return fn(*args, **kwargs)
    except Exception as error:  # noqa: BLE001 - containment is the point
        message = f"{type(error).__name__}: {error}"
        run.failures[name] = message
        _log(ctx, f"step {name} failed: {message}\n{traceback.format_exc()}")
        return None


def _outbox_has(canonical: Path, title: str) -> bool:
    for path in (Path(canonical) / OUTBOX_DIR).glob("*.json"):
        message = read_json(path)
        if isinstance(message, dict) and message.get("title") == title:
            return True
    return False


# --- pause ---


def _pause_title(canonical: Path) -> str:
    return f"Agent paused at {read_json(pause_path(ledger_of(canonical)))['paused_at']}"


def pause_for_preflight(
    ctx: RunContext, queue: Queue, result: PreflightResult, allow_no_remote: bool
) -> None:
    """Pause (C7; idempotent) and publish one help_request for this pause (C6)."""
    canonical = ctx.canonical
    reasons = "; ".join(result.reasons)
    pause(queue, ledger_of(canonical), f"preflight failed: {reasons}")
    with canonical_lock(canonical):
        title = _pause_title(canonical)
        if _outbox_has(canonical, title):
            return
        body = (
            f"queue-keeper preflight failed, so the agent is paused: no new cards start and "
            f"pending cards are unassigned. Reasons: {reasons}. Running cards stop at the next "
            f"identity check. Fix the cause; the agent resumes by itself on the first run "
            f"whose preflight passes."
        )
        publish_runtime(
            canonical,
            "help_request",
            title,
            body,
            [],
            allow_no_remote,
            priority="P0",
            expects_response=True,
        )


def resume_after_preflight(ctx: RunContext, queue: Queue, allow_no_remote: bool) -> list[str]:
    canonical = ctx.canonical
    with canonical_lock(canonical):
        title = _pause_title(canonical)
    resumed = resume(queue, ledger_of(canonical))
    with canonical_lock(canonical):
        body = f"Preflight passes again; {len(resumed)} card(s) were reassigned."
        publish_runtime(canonical, "note", f"Agent resumed ({title})", body, [], allow_no_remote)
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
            title = f"Staged message {message_id} was not published"
            if not _outbox_has(canonical, title):
                body = (
                    f"The staged message comms/staged/{message_id}.json does not satisfy the "
                    f"message envelope, so it stays unpublished:\n\n"
                    + "\n".join(f"- {problem}" for problem in problems)
                )
                publish_runtime(canonical, "note", title, body, [], allow_no_remote)
            write_json(marker, {"id": message_id, "problems": problems})
            commit(canonical, [marker], f"publish: skipped staged {message_id} reported")


def kb_step(canonical: Path, now: datetime.datetime) -> None:
    """Lint apply, compaction and generated views (System Design §3.5); one commit."""
    layout = Layout(canonical)
    with canonical_lock(canonical):
        kb_apply(layout, analyze(layout), now)
        compact(layout, now, pending_reverify=pending_reverification(layout))
        write_generated(layout)
        commit(canonical, [layout.kb], "kb: lint fixes, compaction, generated views")


def _remote_main(canonical: Path) -> str | None:
    try:
        output = gitops.git(canonical, "ls-remote", REMOTE, "refs/heads/main")
    except gitops.GitError:
        return None
    return output.split()[0] if output else None


def push_early(canonical: Path) -> None:
    """Best-effort push after merges (step 7); the final push records the outcome."""
    if REMOTE in gitops.git(canonical, "remote").split():
        with canonical_lock(canonical):
            gitops.push(canonical, REMOTE, PUSH_REFSPECS)


def push_step(ctx: RunContext, allow_no_remote: bool) -> bool | None:
    """Push and record whether the remote main equals HEAD (module docstring, step 13)."""
    canonical = ctx.canonical
    if REMOTE not in gitops.git(canonical, "remote").split():
        if allow_no_remote:
            return None
        raise RuntimeError(f"canonical {canonical} has no {REMOTE!r} remote")
    with canonical_lock(canonical):
        pushed_all = gitops.push(canonical, REMOTE, PUSH_REFSPECS)
        head = gitops.head(canonical)
    remote_head = _remote_main(canonical)
    ok = remote_head == head
    state = _read_state(ctx, PUSH_STATE_FILE, {})
    failures = 0 if ok else int(state.get("consecutive_failures", 0)) + 1
    help_sent = bool(state.get("help_sent")) and not ok
    if ok and not pushed_all:
        _log(ctx, "push: main reached origin but a card branch was rejected")
    if failures >= PUSH_FAILURE_LIMIT and not help_sent:
        with canonical_lock(canonical):
            body = (
                f"queue-keeper could not push canonical main to {REMOTE} {failures} runs in a "
                f"row (remote main {remote_head}, local HEAD {head}). Unpushed work is lost if "
                f"this host is lost. Check the remote URL, credentials and network."
            )
            publish_runtime(
                canonical,
                "help_request",
                f"Push to {REMOTE} failing ({failures} runs)",
                body,
                [],
                allow_no_remote=True,
            )
        help_sent = True
    write_json(
        runtime_dir(ctx) / PUSH_STATE_FILE,
        {
            "ok": ok,
            "head": head,
            "remote_head": remote_head,
            "consecutive_failures": failures,
            "help_sent": help_sent,
            "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        },
    )
    return ok


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
) -> None:
    """Admit committed create requests (module docstring, step 10); one failing request does
    not stop the others."""
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
    run.ops = _step(run, ctx, "ops", apply_ops, ctx, queue) or []


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
    try:
        run.preflight = preflight(env, sm, canonical, now, **preflight_kwargs)
    except Exception as error:  # noqa: BLE001 - a broken preflight stops the run, logged
        run.failures["preflight"] = f"{type(error).__name__}: {error}"
        _log(ctx, f"preflight raised: {error}\n{traceback.format_exc()}")
        return
    for warning in run.preflight.warnings:
        _log(ctx, f"preflight warning: {warning}")
    if not run.preflight.ok:
        run.paused = True
        _log(ctx, f"preflight failed: {run.preflight.reasons}")
        _step(run, ctx, "pause", pause_for_preflight, ctx, queue, run.preflight, allow_no_remote)
        return

    _step(run, ctx, "merge_head", abort_stale_merge, canonical)
    game_version = _step(run, ctx, "game_version", sm.game_version, game.league_id)
    _step(run, ctx, "promotion", promotion.reconcile, ctx, queue, sm)
    run.rebuilt = (
        _step(
            run, ctx, "rebuild", _rebuild_step, ctx, queue, sm, game_version, now, allow_no_remote
        )
        or []
    )

    def publish(target: Path, merge_sha: str, handoff: dict) -> None:
        run.published.append(publish_hook(target, merge_sha, handoff, allow_no_remote))

    store = RecordStore(Layout(canonical))
    run.handoffs = (
        _step(run, ctx, "handoffs", process_handoffs, ctx, queue, store, publish=publish) or []
    )
    _step(run, ctx, "skipped_staged", report_skipped, canonical, run.published, allow_no_remote)
    _step(run, ctx, "kb", kb_step, canonical, now)
    _step(run, ctx, "push", push_early, canonical)
    run.skills = _step(run, ctx, "skills", proposals.sweep_skills, ctx, allow_no_remote) or []
    _step(run, ctx, "routing", route_all, ctx, queue, allow_no_remote=allow_no_remote)
    _step(run, ctx, "admission", admission_step, run, ctx, queue, game, game_version, profile, now)
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
    run.blocked = _step(run, ctx, "blocked", handle_blocked, ctx, queue, allow_no_remote) or []
    run.reraised = _step(run, ctx, "proposals", proposals.reraise, ctx, now, allow_no_remote) or []
    if is_paused(ledger_of(canonical)):
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
