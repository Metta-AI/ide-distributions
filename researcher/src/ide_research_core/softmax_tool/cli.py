"""``research softmax ...``: the platform actions of a researcher, and the reads both profiles use.

League submission is not here: the orchestrator submits through ``research promotion``
(``ide_research_core.promotion``), and the researcher's deny list blocks that group.

Commands (the Softmax backend comes from ``RESEARCH_SOFTMAX``: ``fake`` or the real client;
the game, for its league, from ``RESEARCH_GAME``):

- ``whoami``: the credential's identity (``expires_at`` is null for a ``cga_`` agent token,
  which does not expire); exit 1 (``identity``) unless it is the expected
  (``RESEARCH_PLAYER_ID``) unexpired Player.
- ``credits``: ``{available: true, credits}`` (the account's XP credit balance, readable with
  the Player credential, amendment A10), or ``{available: false, reason}`` when the read fails.
- ``xp create <E> --batch N --attempt N [--batches K]``: launch batch N of a paired evaluation:
  one seeded XP request per (seed, arm) in the batch's slice of ``design.seeds`` (see
  ``xp_specs`` for the mapping, A12). It recovers once, backfills any recovered request missing
  from ``experiment.xp_requests``, creates only the missing requests, and appends
  ``{batch, attempt, seed, arm, request_id, policy}`` for each one as soon as it is created (so
  a platform error or crash mid-batch loses no record), then moves
  ``uploaded|awaiting-credits|awaiting-permit`` to ``evaluating``.
  Before it creates any request it checks the identity and that ``design.baseline_version``
  is still the current champion (``promotion.baseline_problem``; refusals
  ``champion_conflict``, ``champion_unknown``, ``baseline_not_champion``, and nothing is
  created): an experiment whose baseline is not the champion can never promote. It prints
  ``{created, existing, missing, pairing, credit_denied, permit_required,
  league_not_chosen}``, plus ``permit_declined``, ``permit_asks`` and ``credits_short`` on a
  refusal of the coach's. A credit denial stops the batch
  with exit 0 and ``credit_denied: true``: requests already created stay (and are
  recorded), ``missing`` lists the (seed, arm) pairs not created, the experiment goes
  ``awaiting-credits`` (from ``uploaded|evaluating|awaiting-permit``) and ``lifecycle.next_launch_after`` is set
  by ``protocol.retry_backoff`` (A10; base 30 minutes, or ``RESEARCH_CREDIT_BACKOFF_MINUTES`` from
  the worker's environment, read before any request is created; it is read in worker terminals
  only, which see the whole profile ``.env``, so it is deliberately not on
  ``terminal.env_passthrough``: a cron job would not get it); ``lifecycle.credit_denials``
  counts consecutive denials and ``lifecycle.credits_at_denial`` is the balance read right
  after the denial (null when unreadable). The orchestrator's supervisor watches the balance
  and has a researcher relaunch when it covers the remaining requests. The coach's refusals (Softmax hosting) end with exit 0
  too, and the experiment goes ``awaiting-permit`` (from ``uploaded|evaluating|awaiting-credits``)
  with ``lifecycle.permit_wait`` = ``{kind, batch, attempt, batches, league_id, reason, asks,
  announced, refusals, since, retry_minutes, not_before, credits_short}``. A permit refusal (the
  IDE Controls ask before XP requests, ``permit_required: true``) does not stop the batch: every
  request is tried, so one run files every ask, and ``asks`` lists one ``{request, seed, arm,
  ask, state, reason}`` per refused request (``state``: ``waiting``, ``declined`` or
  ``over_cap``, from the server's reason; ``not_before`` is the next UTC day when one is over
  today's cap). A league refusal (no league or another league chosen, ``league_not_chosen:
  true``) stops the batch. The open asks are merged by request, never replaced: a rerun keeps
  every saved ask, a request it refuses again replaces its entry (the same ask, or a new one
  the server filed for it), and only requests now created drop out (``_merge_asks``).
  ``announced`` is the set the help request is about; it changes only when the server files a
  new ask. Any open ask keeps the experiment ``awaiting-permit``: a credit denial in the same
  run (``credit_denied: true`` too) is recorded as ``permit_wait.credits_short`` = ``{reason,
  credit_denials, credits_at_denial, next_launch_after}`` and does not change the status; only
  a batch with no open ask goes ``awaiting-credits``. The orchestrator's supervisor reads each
  ask's answer and has a researcher relaunch once all are yes and, with ``credits_short``, the
  credits cover it; the relaunch sends the same body and idempotency key, so the server
  decides it against the same ask. ``lifecycle.next_launch_after`` is the fallback for when
  the answers cannot be read (and for a league refusal): the credit backoff, counted by
  ``refusals`` of the same asks only. A ``stopped`` (the coach declined it) or ``reviewed``
  experiment, or one whose wait records a declined ask, is refused (``experiment_closed``)
  before anything is created. A refusal that says the coach said no ends the batch at once
  (``permit_declined: true``). Each refusal is saved to ``permit_wait`` before the next
  request is sent, so a later failure or crash in the batch loses no ask the server already
  filed. While permit asks are open, only their exact ``--batch``/``--attempt``/``--batches``
  may run (``permit_batch_mismatch``), and only in the saved ``permit_wait.league_id``: a
  changed game league is refused (``permit_league_changed``) before anything is sent.
  Rerunning the same ``--batch``/``--attempt`` later creates only the missing requests. A new
  attempt (one with no request yet in the batch) is a retry, and
  the only reason to retry is a failed or cancelled request, so it is refused unless it is
  the next number (``bad_attempt``), the batch's latest attempt has every request
  (``attempt_incomplete``: rerun that attempt), and at least one of those requests is
  ``failed`` (``attempt_not_needed``; read with ``xp_status``). So no relaunch, however many
  run one after another, repeats a healthy attempt. Two relaunchers at the same moment run
  one after the other (the experiment lock, below), so the second sees what the first
  created. A changed ``--batches`` mapping is refused when it conflicts with recovered
  requests. New attempts reuse healthy requests across earlier attempts and retry failed
  requests only; successful evidence remains usable.
- ``xp status <E>``, ``xp recover <E>``, ``xp artifacts <E> --dest DIR``: over every request
  tagged for the experiment (``Softmax.xp_recover``); artifacts land in ``DIR/<request id>/``.
- ``xp evidence <E>``: evidence and episode rows for every finished request that has none
  yet, written into this worktree (``softmax_tool.evidence``). Prints ``{experiment,
  collected, already, pending, failed, complete, rows}``; ``complete`` means the review can
  run ``stats ingest`` on ``rows``.
- ``upload <E> --name NAME --build-dir D``: ``softmax_tool.upload``.
- ``upload-build --name NAME --build-dir D``: upload a checked build as a version with no experiment;
  prints ``{version_id, platform_policy_version_id}``. Supports explicit build/upload-only requests
  and starter builds. It does not evaluate or promote; starter promotion remains explicit.
- ``leaderboard``: ``{league_id, player_id, current_champion, conflict}``. The champion is the
  policy the league shows competing for our player (A9; null: none). ``conflict`` is null, or
  why the league and the loop's recorded champion disagree (``promotion.conflict``).
- ``game-version``: ``{league_id, game_version}``, the league's current game version
  (``Softmax.game_version``; the version noise records and designs must match, C10).
- ``standings``: ``{league_id, rows}``, the league standings (``Softmax.standings``), read-only.
- ``league-episodes [--league L] --limit N --out rows.jsonl``: up to N completed league
  episodes (``Softmax.league_episodes``), each mapped through the game's evaluate binding
  into EpisodeRows (``softmax_tool.league``), written as JSONL for
  ``stats ingest --league``. Prints ``{league_id, episodes, rows, out, game_versions}``.
  ``--league`` defaults to the game's league. Episodes are evaluated as they arrive (the real
  client fetches 4 at a time); a progress line goes to stderr every 10 episodes, and ``--out``
  is written once, when the read is complete.

Record writes go to the worker worktree (``HERMES_KANBAN_WORKSPACE``) only.

Experiment lock. ``xp create`` and ``upload`` hold a per-experiment lock for the whole
command: ``fcntl.flock`` on ``locks/xp-<E>.lock`` in the bare canonical repository, shared by
every researcher on the host. The holder writes ``{worker, profile, pid, command,
started_at}`` into the file while it holds the lock and empties it before release. A
second command for the same experiment waits up to ``LOCK_WAIT_S`` (60 s; below Hermes'
180 s foreground terminal timeout), then refuses with ``busy`` and does nothing; ``details``
carries ``holder`` and ``own_run`` (the holder is this worktree, for example an earlier call
still running in the background: wait for it, do not give up the card). The kernel
releases the lock when the process exits, so a crashed worker never leaves it held. Do not
delete ``locks/`` while workers run: a new file would let a second holder in. The
experiment argument must be an ``E-`` id (``bad_arguments`` otherwise), because it names the
lock file.

Identity is the spawn-time one (C6, C7). Each command uses the credential and expected player
id in its own environment (what Hermes gave the worker when it started) for ``whoami`` and the
mutating call. The profile ``.env`` is read only to refuse: when it sets ``IDENTITY_ENV`` names
to other values (the operator changed the player since this worker started), every command
fails with ``identity``. The ``.env`` is never a
source of identity, because a worker could write it.

Prints one JSON envelope: ``{"ok": true, "data": ...}`` or ``{"ok": false, "error": {"reason",
"message", "details"?}}``. Exit codes: 0 done (including a credit denial or a refusal by the coach), 1 refused or failed
(``identity``, a guard's reason, ``platform``, ...), 2 bad arguments or no profile context,
3 the pinned softmax/coworld executable is missing.
"""

import argparse
import itertools
import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import frontmatter
from pydantic import ValidationError

from ide_research_core import promotion, refs, runctx, workspace
from ide_research_core.cli import register_group
from ide_research_core.games.bindings import binding_evaluate
from ide_research_core.games.loader import GameError, load_game
from ide_research_core.ids import parse_id
from ide_research_core.io import gitops
from ide_research_core.io.docs import write_text
from ide_research_core.io.dotenv import env_file_value
from ide_research_core.io.locks import LockTimeout, file_lock
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordNotFound, RecordProblems, RecordStore
from ide_research_core.softmax_tool import evidence
from ide_research_core.softmax_tool.discovery import Discovery
from ide_research_core.softmax_tool.identity import IdentityError, identity_problems
from ide_research_core.softmax_tool.league import episode_rows
from ide_research_core.softmax_tool.platform_cli import SoftmaxCliError, ToolMissing
from ide_research_core.softmax_tool.protocol import (
    BACKOFF_BASE,
    PERMIT_DECLINED,
    PERMIT_OVER_CAP,
    CreditDenied,
    LeagueNotChosen,
    PermitRequired,
    Refused,
    XpHandle,
    read_credits,
    retry_backoff,
)
from ide_research_core.softmax_tool.protocol import from_env as softmax_from_env
from ide_research_core.softmax_tool.upload import upload, upload_build, worker_store
from ide_research_core.softmax_tool.xp import ARMS, ArtifactsIncomplete, tag
from ide_research_core.stats.design import DesignError, EvaluationDesign

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS, EXIT_TOOL_MISSING = 0, 1, 2, 3
_REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ lives at the project root
GAME_ENV = "RESEARCH_GAME"
PLAYER_ENV = "RESEARCH_PLAYER_ID"
IDENTITY_ENV = ("SOFTMAX_PLAYER_CREDENTIAL", PLAYER_ENV)
BACKOFF_ENV = "RESEARCH_CREDIT_BACKOFF_MINUTES"
LOCK_WAIT_S = 60.0  # how long a command waits for another process's experiment lock
# Statuses whose evaluation is over: ``xp create`` refuses them (``experiment_closed``).
CLOSED_EXPERIMENT = ("stopped", "reviewed")
# ``xp create``'s refusal flags when nothing refused the batch.
NOT_REFUSED = {"credit_denied": False, "permit_required": False, "league_not_chosen": False}


class BadArguments(ValueError):
    pass


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _fail(reason: str, message: str, code: int, details: dict | None = None) -> int:
    error = {"reason": reason, "message": message}
    if details:
        error["details"] = details
    _emit({"ok": False, "error": error})
    return code


def check_identity_unchanged(hermes_home: Path) -> None:
    """Raise IdentityError when the profile ``.env`` sets an ``IDENTITY_ENV`` name to a value
    other than this process's (module docstring). Values are never printed."""
    changed = [
        name
        for name in IDENTITY_ENV
        if (value := env_file_value(Path(hermes_home) / ".env", name)) is not None
        and value != os.environ.get(name)
    ]
    if changed:
        raise IdentityError(
            f"profile identity changed since this worker started ({', '.join(changed)}); "
            "no Softmax action until a new worker starts with the new identity"
        )


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            ctx = runctx.from_env()
        except KeyError as error:
            return _fail("bad_arguments", f"{error.args[0]} is not set", EXIT_BAD_ARGS)
        try:
            check_identity_unchanged(ctx.hermes_home)
            sm = softmax_from_env(os.environ, ctx.canonical)
            data = handler(ctx, sm, args)
        except (BadArguments, GameError, ValidationError) as error:
            return _fail("bad_arguments", str(error), EXIT_BAD_ARGS)
        except Refused as error:
            return _fail(error.reason, str(error), EXIT_FAILED, error.details)
        except IdentityError as error:
            return _fail("identity", str(error), EXIT_FAILED)
        except ToolMissing as error:
            return _fail("tool_missing", str(error), EXIT_TOOL_MISSING)
        except ArtifactsIncomplete as error:
            return _fail("artifacts_incomplete", str(error), EXIT_FAILED)
        except (RecordNotFound, RecordProblems, DesignError) as error:
            return _fail("records", str(error), EXIT_FAILED)
        except (SoftmaxCliError, RuntimeError, OSError, ValueError) as error:
            return _fail("platform", f"{type(error).__name__}: {error}", EXIT_FAILED)
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


@contextmanager
def experiment_lock(ctx, experiment: str, command: str) -> Iterator[None]:
    """Hold the experiment's cross-process lock for ``command`` (module docstring), or refuse
    ``busy`` naming the holder. Not re-entrant: a command holding it must not call another
    locked command in the same process (it would wait, then refuse ``busy``)."""
    try:
        prefix, _ = parse_id(experiment)
    except ValueError:
        prefix = None
    if prefix != "E":
        raise BadArguments(f"{experiment!r} is not an experiment id (E-<ULID>)")
    # In the bare repository, so it holds across every researcher's clone on this host.
    path = workspace.canonical_of(ctx.canonical) / "locks" / f"xp-{experiment}.lock"
    with ExitStack() as held:
        try:  # only the acquisition: a LockTimeout from the command itself is not "busy"
            handle = held.enter_context(file_lock(path, timeout_s=LOCK_WAIT_S))
        except LockTimeout:
            raise _busy(ctx, experiment, _read_holder(path)) from None
        record = {
            "worker": str(ctx.worker) if ctx.worker else None,
            "profile": ctx.profile,
            "pid": os.getpid(),
            "command": command,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        handle.truncate(0)  # the lock is on the open handle, so rewriting the file is safe
        handle.write(json.dumps(record, sort_keys=True))
        handle.flush()
        try:
            yield
        finally:
            handle.truncate(0)  # before the unlock: a stale holder is never reported


def _read_holder(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # empty: the holder has not written itself yet
        return None
    return record if isinstance(record, dict) else None


def _busy(ctx, experiment: str, holder: dict | None) -> Refused:
    own_run = bool(holder and ctx.worker and holder.get("worker") == str(ctx.worker))
    if holder is None:
        who = "another process (holder not recorded yet)"
    else:
        who = (
            f"{holder.get('profile')} in {holder.get('worker')} "
            f"(pid {holder.get('pid')}, {holder.get('command')} since {holder.get('started_at')})"
        )
    if own_run:
        advice = (
            "This is your own earlier command, still running (for example a terminal call that "
            "went to the background). Wait for it to finish and use its result; do not rerun "
            "it and do not finish the card partial"
        )
    else:
        advice = (
            "Another card works on this experiment. Do not duplicate its work; ask the "
            "orchestrator with a question: comment on your card"
        )
    return Refused(
        "busy",
        f"{experiment} is locked by {who}; waited {LOCK_WAIT_S:g} s. {advice}",
        {"holder": holder, "own_run": own_run},
    )


def _game():
    name = os.environ.get(GAME_ENV)
    if not name:
        raise BadArguments(f"{GAME_ENV} is not set")
    return load_game(_REPO_ROOT, name)


def _league_id() -> str:
    return _game().league_id


# --- XP mapping (pure) ---


def backoff_base(env) -> timedelta:
    """The first backoff: ``BACKOFF_ENV`` minutes (a positive finite number) when set, else 30.
    Worker terminals only; not on ``terminal.env_passthrough`` (module docstring)."""
    value = (env.get(BACKOFF_ENV) or "").strip()
    if not value:
        return BACKOFF_BASE
    try:
        minutes = float(value)
    except ValueError:
        minutes = 0.0
    if not (math.isfinite(minutes) and minutes > 0):
        raise BadArguments(f"{BACKOFF_ENV} must be a positive number of minutes, got {value!r}")
    return timedelta(minutes=minutes)


def _opponent(entry: str) -> dict:
    player = {"random": True} if entry == "random" else {"policy_ref": entry}
    return {"slot": -1, "player": player}


def _roster(design: EvaluationDesign, policy: str, seed: int) -> list[dict]:
    """Our policy in each ``design.seats`` slot, then the seed's opponents
    (``design.opponents_for(seed)``: its sampled line-up, or the fixed set, A12) in order in
    the remaining slots (ascending, skipping our seats). Fixed slots keep a pair's seating
    identical; with ``slot: -1`` the platform would rotate open seats per episode."""
    roster = [{"slot": seat, "player": {"policy_ref": policy}} for seat in design.seats]
    free_slots = (slot for slot in itertools.count() if slot not in design.seats)
    for entry, slot in zip(design.opponents_for(seed), free_slots, strict=False):
        roster.append({**_opponent(entry), "slot": slot})
    return roster


def xp_specs(
    design: dict, binding: dict, league_id: str, batch: int, batches: int
) -> list[tuple[int, str, dict]]:
    """``(seed, arm, XP spec)`` for every request of ``batch`` (1-based) of ``batches`` (A12).

    Mapping from the frozen evaluation design:

    - ``design.seeds`` is split into ``batches`` contiguous slices of ceil(len(seeds)/batches);
      ``batch`` takes its slice (an empty slice is refused as ``empty_batch``). Each seed gets two requests, candidate first, then baseline.
    - Each request has ``num_episodes: 1`` and ``game_config_overrides: {"seed": seed}``: the
      platform pins every episode of a request to its override seed, so the two arms of a seed
      play the same board. The tag ``ar:<E>:b<batch>:a<attempt>:s<seed>:<arm>`` is unique.
    - The candidate arm plays the binding's platform id, the baseline arm
      ``design.baseline_version``, each in every ``design.seats`` slot.
    - Opponents fill the other slots in order as fixed seats: the seed's line-up sampled from
      the league at design time (``design.lineups``, A12), or the design's fixed
      ``opponents``. A policy ref is identical across the pair; ``"random"`` is sampled per
      request by the platform, so pairing is then seed-only (``EvaluationDesign.pairing``).
      The roster must fill the game's seat count.
    """
    if batches < 1 or not 1 <= batch <= batches:
        raise BadArguments(f"--batch must be between 1 and --batches ({batches}), got {batch}")
    parsed = EvaluationDesign.from_dict(design)  # validate(): unique seeds, at least n_min
    size = math.ceil(len(parsed.seeds) / batches)
    seeds = parsed.seeds[(batch - 1) * size : batch * size]
    if not seeds:
        raise Refused(
            "empty_batch",
            f"batch {batch} of {batches} has no seeds ({len(parsed.seeds)} seeds in slices of "
            f"{size}); use fewer --batches",
        )
    policies = {
        "candidate": binding["platform_policy_version_id"],
        "baseline": parsed.baseline_version,
    }
    return [
        (
            seed,
            arm,
            {
                "league_id": league_id,
                "num_episodes": 1,
                "game_config_overrides": {"seed": seed},
                "roster": _roster(parsed, policies[arm], seed),
            },
        )
        for seed in seeds
        for arm in ARMS
    ]


# --- handlers ---


def _whoami(ctx, sm, args) -> dict:
    identity = sm.whoami()
    problems = identity_problems(identity, os.environ.get(PLAYER_ENV, ""), datetime.now(UTC))
    if problems:
        raise IdentityError("; ".join(problems))
    return {
        "subject_type": identity.subject_type,
        "player_id": identity.player_id,
        "expires_at": identity.expires_at.isoformat() if identity.expires_at else None,
        "problems": problems,
    }


def _credits(ctx, sm, args) -> dict:
    try:
        return {"available": True, "credits": sm.credits()}
    except SoftmaxCliError as error:
        return {"available": False, "reason": str(error)}


def _credit_shortfall(
    lifecycle: dict, credits: float | None, base: timedelta, now: datetime
) -> dict:
    """The facts of a credit denial (A10): the consecutive ``credit_denials``, the balance read
    right after it (``credits_at_denial``, null when unreadable) and the backoff's end."""
    denials = int(lifecycle.get("credit_denials", 0)) + 1
    after = now + retry_backoff(denials, base)
    return {
        "credit_denials": denials,
        "credits_at_denial": credits,
        "next_launch_after": after.isoformat(timespec="seconds"),
    }


def _credit_denied(
    store, experiment: str, meta: dict, reason: str, base: timedelta, credits: float | None
) -> dict:
    """``awaiting-credits`` with the backoff (A10). Only when no permit ask of the batch is
    open: with open asks the experiment stays ``awaiting-permit`` (``_awaiting_coach``)."""
    lifecycle = dict(meta.get("lifecycle") or {})
    facts = _credit_shortfall(lifecycle, credits, base, datetime.now(UTC))
    # A league wait (or one whose asks were all granted) says nothing more once credits stop it.
    patch: dict = {"lifecycle": {**lifecycle, **facts, "permit_wait": None}}
    if meta["status"] in ("uploaded", "evaluating", "awaiting-permit"):
        patch["status"] = "awaiting-credits"
    store.update(experiment, patch)
    return {
        **NOT_REFUSED,
        "credit_denied": True,
        "reason": reason,
        "next_launch_after": facts["next_launch_after"],
        "status": patch.get("status", meta["status"]),
    }


def _next_utc_day(now: datetime) -> datetime:
    """The start of the next UTC day: when an over-the-cap spend can be covered again."""
    return datetime.combine(now.astimezone(UTC).date() + timedelta(days=1), dtime.min, UTC)


def _merge_asks(saved: list[dict], fresh: list[dict], done: set) -> list[dict]:
    """The batch's open asks after this run. The saved asks are kept by request (``request``,
    the request's tag): this run's refusal of a request replaces its saved entry (the same ask
    again, or a new one the server filed for it), and a request now created (``done``: its
    (seed, arm)) drops out. An ask is never dropped because this run did not reach its request
    (a credit denial, a league refusal or a no stopped the batch first)."""
    by_request = {entry["request"]: entry for entry in saved}
    for entry in fresh:
        by_request[entry["request"]] = entry
    return [entry for entry in by_request.values() if (entry["seed"], entry["arm"]) not in done]


def _permit_wait(
    lifecycle: dict,
    args,
    refusal: LeagueNotChosen | None,
    asks: list[dict],
    over_cap: bool,
    league_id: str,
    base: timedelta,
    now: datetime,
) -> tuple[dict, datetime]:
    """``lifecycle.permit_wait`` for the coach's refusal of this batch, and the fallback
    relaunch time (module docstring, ``xp create``). ``asks`` are the merged open asks
    (``_merge_asks``); ``over_cap``: this run's refusals include one over today's cap."""
    kind = "league" if refusal is not None else "permit"
    previous = lifecycle.get("permit_wait") or {}
    keys = sorted(entry["ask"] or "" for entry in asks)
    announced = previous.get("announced") or sorted(
        entry.get("ask") or "" for entry in previous.get("asks") or []
    )
    # The same wait while the server filed no new ask: asks only drop out as requests are
    # created. A new ask is a new set: its own help request, reminder clock and backoff.
    same = previous.get("kind") == kind and set(keys) <= set(announced)
    # The fallback backoff counts the refusals of these asks only (a new wait starts over).
    refusals = int(previous.get("refusals", 0)) + 1 if same else 1
    since = previous.get("since") if same and previous.get("since") else now.isoformat()
    after = now + retry_backoff(refusals, base)
    wait = {
        "kind": kind,
        "batch": args.batch,
        "attempt": args.attempt,
        "batches": args.batches,
        "league_id": league_id,
        "reason": refusal.reason if refusal is not None else None,
        "asks": asks,
        "announced": announced if same else keys,  # the asks the help request is about
        "refusals": refusals,
        "since": since,  # when the coach was first asked these asks (the reminder clock)
        "retry_minutes": int(base.total_seconds() // 60),
        "not_before": None,
        "credits_short": None,
    }
    if over_cap:
        wait["not_before"] = _next_utc_day(now).isoformat(timespec="seconds")
        after = max(after, _next_utc_day(now))
    elif any(entry["state"] == PERMIT_OVER_CAP for entry in asks):
        wait["not_before"] = previous.get("not_before")  # not tried again this run
    return wait, after


def _awaiting_coach(
    store,
    experiment: str,
    meta: dict,
    previous_wait: dict | None,
    args,
    refusal: LeagueNotChosen | None,
    asks: list[dict],
    over_cap: bool,
    league_id: str,
    base: timedelta,
    shortfall: tuple[str, float | None] | None,
) -> dict:
    """The coach refused the batch: the league choice (``refusal``), or permit asks are open
    (``asks``, merged). ``awaiting-permit`` with the wait recorded (module docstring, ``xp
    create``). ``shortfall`` is ``(reason, balance)`` when credits also ran out in this run: it
    is recorded as ``permit_wait.credits_short`` and the status stays ``awaiting-permit``.
    ``previous_wait`` is the wait before this run: saving the wait again in the same run (once
    per refusal) gives the same counters, ``since`` and ``announced``."""
    lifecycle = {**(meta.get("lifecycle") or {}), "permit_wait": previous_wait}
    now = datetime.now(UTC)
    wait, after = _permit_wait(lifecycle, args, refusal, asks, over_cap, league_id, base, now)
    next_launch_after = after.isoformat(timespec="seconds")
    patched = {**lifecycle, "next_launch_after": next_launch_after, "permit_wait": wait}
    if shortfall is not None:
        facts = _credit_shortfall(lifecycle, shortfall[1], base, now)
        wait["credits_short"] = {"reason": shortfall[0], **facts}
        patched["credit_denials"] = facts["credit_denials"]
        patched["credits_at_denial"] = facts["credits_at_denial"]
    patch: dict = {"lifecycle": patched}
    if meta["status"] in ("uploaded", "evaluating", "awaiting-credits"):
        patch["status"] = "awaiting-permit"
    store.update(experiment, patch)
    kind = wait["kind"]
    return {
        **NOT_REFUSED,
        "credit_denied": shortfall is not None,
        "permit_required": kind == "permit",
        "league_not_chosen": kind == "league",
        "permit_declined": any(entry["state"] == PERMIT_DECLINED for entry in asks),
        "reason": refusal.reason if refusal is not None else asks[0]["reason"],
        "permit_asks": asks,
        "credits_short": wait["credits_short"],
        "next_launch_after": next_launch_after,
        "status": patch.get("status", meta["status"]),
    }


def _xp_entry(handle: XpHandle, policy: str | None) -> dict:
    return {
        "batch": handle.batch,
        "attempt": handle.attempt,
        "seed": handle.seed,
        "arm": handle.arm,
        "request_id": handle.request_id,
        "policy": policy,
    }


def _record_xp(store, experiment: str, entries: list[dict]) -> dict:
    """Append entries whose request_id is not yet in ``xp_requests``; return the fresh meta."""
    meta, _ = store.load(experiment)
    requests = meta.get("xp_requests") or []
    recorded = {r.get("request_id") for r in requests}
    new = [entry for entry in entries if entry["request_id"] not in recorded]
    if new:
        store.update(experiment, {"xp_requests": [*requests, *new]})
        meta, _ = store.load(experiment)
    return meta


def check_new_attempt(
    sm, recovered: list[XpHandle], planned: list, batch: int, attempt: int
) -> None:
    """Refuse a new ``attempt`` of ``batch`` unless it is the next number, the latest attempt
    is complete, and one of its requests failed (module docstring). ``planned`` is
    ``xp_specs`` for the batch; ``sm`` reads the latest attempt's statuses."""
    attempts = {handle.attempt for handle in recovered if handle.batch == batch}
    if not attempts:
        if attempt != 1:
            raise Refused("bad_attempt", "The first attempt must be 1")
        return
    latest = max(attempts)
    if attempt < latest:
        raise Refused("stale_attempt", f"Resume the latest attempt {latest}, not {attempt}")
    if attempt == latest:
        return
    if attempt != latest + 1:
        raise Refused(
            "bad_attempt",
            f"batch {batch} has attempts {sorted(attempts)}; a new attempt must be {latest + 1}",
        )
    latest_handles = evidence.latest_handles([h for h in recovered if h.batch == batch])
    have = {
        (h.seed, h.arm)
        for h in latest_handles
        if h.attempt == latest or sm.xp_status(h).state != "failed"
    }
    missing = [(seed, arm) for seed, arm, _ in planned if (seed, arm) not in have]
    if missing:
        raise Refused(
            "attempt_incomplete",
            f"batch {batch} attempt {latest} still lacks {len(missing)} request(s); rerun "
            f"xp create with --attempt {latest} to create only those (a new attempt would "
            f"repeat every request)",
        )
    if not any(sm.xp_status(handle).state == "failed" for handle in latest_handles):
        raise Refused(
            "attempt_not_needed",
            f"batch {batch} attempt {latest} has every request and none failed; a new attempt "
            f"would repeat them all. Nothing to relaunch: wait for the review card",
        )


def _check_permit_wait(experiment: str, meta: dict, args, league_id: str) -> None:
    """While the experiment waits on open permit asks (``awaiting-permit`` with
    ``lifecycle.permit_wait.asks``), only the exact batch they were filed for may run: the same
    ``--batch``, ``--attempt`` and ``--batches`` give the same requests and idempotency keys, so
    each one replays against its own ask (``permit_batch_mismatch`` otherwise). The league is
    part of each request body and of the ask's identity, so the game's configured league
    (``league_id``) must still be the saved ``permit_wait.league_id``; a changed configuration is
    refused (``permit_league_changed``) instead of sending other bodies under the same keys. A
    wait with a declined ask is final (``experiment_closed``): the orchestrator stops the experiment."""
    wait = (meta.get("lifecycle") or {}).get("permit_wait") or {}
    if not wait.get("asks") or meta.get("status") != "awaiting-permit":
        return
    if any(entry.get("state") == PERMIT_DECLINED for entry in wait.get("asks") or []):
        raise Refused(
            "experiment_closed",
            f"the coach declined an XP request of {experiment}; its evaluation does not run "
            "again",
        )
    saved = (wait.get("batch"), wait.get("attempt"), wait.get("batches"))
    if saved != (args.batch, args.attempt, args.batches):
        batch, attempt, batches = saved
        raise Refused(
            "permit_batch_mismatch",
            f"{experiment} waits on the coach's permit asks for --batch {batch} --attempt "
            f"{attempt} --batches {batches}; run exactly that, so the approved requests replay "
            "under the same keys",
            {"batch": batch, "attempt": attempt, "batches": batches},
        )
    saved_league = wait.get("league_id")
    if saved_league and saved_league != league_id:
        raise Refused(
            "permit_league_changed",
            f"{experiment} waits on the coach's permit asks for XP requests in league "
            f"{saved_league}, but the game is now configured for {league_id}. Requests for "
            "another league would not match the approved asks. Restore the game's league "
            f"({saved_league}) to replay them; nothing was sent",
            {"saved_league": saved_league, "configured_league": league_id},
        )


def _closed_in_canonical(ctx, experiment: str) -> str | None:
    """The experiment's status as committed in canonical when it is closed (``stopped``,
    ``reviewed``), else None. The orchestrator stops an experiment in canonical, not in this worker's
    worktree, so the worktree copy cannot tell a running relaunch that the coach said no."""
    # The bare repository's main, not this clone's checkout: the clone moves only when it
    # integrates, and a stop lands on main while a batch runs. The file name (it may carry a
    # slug) is looked up at the same commit.
    bare = workspace.canonical_of(Path(ctx.canonical))
    main = gitops.git(bare, "rev-parse", workspace.MAIN)
    try:
        rel = refs._record_path(bare, main, "experiment", experiment)
    except refs.ResolveError:  # not merged to canonical yet
        return None
    content = gitops.show(bare, main, rel)
    status = frontmatter.loads(content.decode("utf-8")).metadata.get("status")
    return status if status in CLOSED_EXPERIMENT else None


def _xp_create(ctx, sm, args) -> dict:
    base = backoff_base(os.environ)
    with experiment_lock(ctx, args.experiment, "xp create"):
        return _xp_create_locked(ctx, sm, args, base)


def _xp_create_locked(ctx, sm, args, base: timedelta) -> dict:
    store = worker_store(ctx)
    experiment = args.experiment
    meta, _ = store.load(experiment)
    if meta.get("status") in CLOSED_EXPERIMENT:
        # A stopped experiment's ask was declined: creating again would only ask the coach again.
        raise Refused(
            "experiment_closed",
            f"{experiment} is {meta['status']}; its evaluation does not run again",
        )
    _check_permit_wait(experiment, meta, args, _league_id())
    # The wait before this run: the base of every merge and of the wait's counters, however
    # often this run saves the wait (each refusal is saved before the next request is sent).
    previous_wait = (meta.get("lifecycle") or {}).get("permit_wait")
    open_asks = (
        ((previous_wait or {}).get("asks") or []) if meta["status"] == "awaiting-permit" else []
    )
    binding = meta.get("candidate_binding")
    if not binding:
        raise Refused("no_binding", f"{experiment} has no candidate_binding; upload first")
    design = meta["evaluation_design"]
    planned = xp_specs(design, binding, _league_id(), args.batch, args.batches)
    policies = {
        "candidate": binding["platform_policy_version_id"],
        "baseline": design["baseline_version"],
    }
    # Recover once: a relaunch (after a credit denial, a platform error or a crash) creates only
    # what is missing, and requests the platform has but the record lacks are backfilled first.
    recovered = sm.xp_recover(experiment)
    batch_size = math.ceil(len(design["seeds"]) / args.batches)
    assignment = {seed: index // batch_size + 1 for index, seed in enumerate(design["seeds"])}
    if any(h.seed not in assignment or assignment[h.seed] != h.batch for h in recovered):
        raise Refused(
            "batch_partition_changed",
            "The requested batch layout disagrees with existing requests. Use the original "
            "layout; changing it could evaluate the same seed again under a new request key.",
        )
    _record_xp(store, experiment, [_xp_entry(h, policies.get(h.arm)) for h in recovered])
    check_new_attempt(sm, recovered, planned, args.batch, args.attempt)
    existing = {
        (handle.seed, handle.arm): handle
        for handle in recovered
        if (handle.batch, handle.attempt) == (args.batch, args.attempt)
    }
    prior = evidence.latest_handles(
        [h for h in recovered if h.batch == args.batch and h.attempt < args.attempt]
    )
    for handle in prior:
        key = (handle.seed, handle.arm)
        if key not in existing and sm.xp_status(handle).state != "failed":
            existing[key] = handle
    if any((seed, arm) not in existing for seed, arm, _ in planned):
        # Identity first: the champion read is per player, so a wrong credential would
        # otherwise show up as "no champion" (the create re-checks it anyway, C6).
        expected = os.environ.get(PLAYER_ENV, "")
        problems = identity_problems(sm.whoami(), expected, datetime.now(UTC))
        if problems:
            raise IdentityError("; ".join(problems))
        problem = promotion.baseline_problem(
            ctx.canonical, sm, _league_id(), expected, design["baseline_version"]
        )
        if problem is not None:
            raise Refused(*problem)
    created: list[dict] = []
    denial: CreditDenied | LeagueNotChosen | None = None
    fresh: list[dict] = []  # one per request refused for a permit in this run; the batch goes on

    def hold(league: LeagueNotChosen | None, shortfall) -> dict:
        """Save the wait with every ask filed so far (``_awaiting_coach``)."""
        done = set(existing) | {(entry["seed"], entry["arm"]) for entry in created}
        current, _ = store.load(experiment)
        over_cap = any(entry["state"] == PERMIT_OVER_CAP for entry in fresh)
        asks = _merge_asks(open_asks, fresh, done)
        return _awaiting_coach(
            store,
            experiment,
            current,
            previous_wait,
            args,
            league,
            asks,
            over_cap,
            _league_id(),
            base,
            shortfall,
        )

    for seed, arm, spec in planned:
        if (seed, arm) in existing:
            continue
        # Before each request: the coach may have said no since this run began (the orchestrator then
        # stops the experiment in canonical). Nothing more is sent: no credits spent on the
        # approved requests, and no new ask filed for the declined one.
        closed = _closed_in_canonical(ctx, experiment)
        if closed is not None:
            raise Refused(
                "experiment_closed",
                f"{experiment} is {closed} in canonical (the coach declined it while this "
                "batch ran); nothing more is sent",
            )
        result = sm.xp_create(
            experiment,
            args.batch,
            args.attempt,
            spec,
            seed=seed,
            arm=arm,
            known=recovered,
        )
        if isinstance(result, PermitRequired):
            # Each request has its own ask: file them all in this run, so the coach sees the
            # whole batch at once. A refused request holds no credits. A no ends the batch at
            # once (H2): nothing more is submitted, and the wait records it, so the orchestrator stops
            # the experiment and ``xp create`` refuses it from now on.
            request = tag(experiment, args.batch, args.attempt, seed, arm)
            fresh.append(
                {
                    "request": request,
                    "seed": seed,
                    "arm": arm,
                    "ask": result.ask,
                    "state": result.state,
                    "reason": result.reason,
                }
            )
            # Saved before the next request: the server keeps the ask, so a later failure or
            # crash in this batch must not lose it.
            hold(None, None)
            if result.state == PERMIT_DECLINED:
                break
            continue
        if isinstance(result, CreditDenied | LeagueNotChosen):
            denial = result  # the whole batch: stop
            break
        entry = _xp_entry(result, policies[arm])
        # Record at once, so a later failure in this batch cannot lose it.
        _record_xp(store, experiment, [entry])
        created.append(entry)
    done = set(existing) | {(entry["seed"], entry["arm"]) for entry in created}
    missing = [{"seed": seed, "arm": arm} for seed, arm, _ in planned if (seed, arm) not in done]
    report = {
        "experiment": experiment,
        "batch": args.batch,
        "attempt": args.attempt,
        "pairing": EvaluationDesign.from_dict(design).pairing,
        "created": created,
        "existing": [
            _handle_dict(existing[(seed, arm)])
            for seed, arm, _ in planned
            if (seed, arm) in existing
        ],
        "missing": missing,
    }
    meta, _ = store.load(experiment)
    asks = _merge_asks(open_asks, fresh, done)
    credits_out = isinstance(denial, CreditDenied)
    if isinstance(denial, LeagueNotChosen) or asks:
        # Open asks keep the experiment awaiting-permit, also when credits ran out too.
        shortfall = (denial.reason, read_credits(sm)) if credits_out else None
        league = denial if isinstance(denial, LeagueNotChosen) else None
        return {**report, **hold(league, shortfall)}
    if credits_out:
        credits = read_credits(sm)
        return {
            **report,
            **_credit_denied(store, experiment, meta, denial.reason, base, credits),
        }

    patch: dict = {}
    if meta["status"] in ("uploaded", "awaiting-credits", "awaiting-permit"):
        patch["status"] = "evaluating"
    lifecycle = meta.get("lifecycle") or {}
    if (
        lifecycle.get("credit_denials")
        or lifecycle.get("next_launch_after")
        or lifecycle.get("permit_wait")
    ):
        patch["lifecycle"] = {
            **lifecycle,
            "credit_denials": 0,
            "next_launch_after": None,
            "credits_at_denial": None,
            "permit_wait": None,
        }
    if patch:
        store.update(experiment, patch)
    return {**report, **NOT_REFUSED}


def _handle_dict(handle: XpHandle) -> dict:
    return {
        "request_id": handle.request_id,
        "experiment": handle.experiment,
        "batch": handle.batch,
        "attempt": handle.attempt,
        "seed": handle.seed,
        "arm": handle.arm,
    }


def _xp_recover(ctx, sm, args) -> list[dict]:
    return [_handle_dict(handle) for handle in sm.xp_recover(args.experiment)]


def _xp_status(ctx, sm, args) -> list[dict]:
    rows = []
    for handle in sm.xp_recover(args.experiment):
        status = sm.xp_status(handle)
        rows.append(
            {
                **_handle_dict(handle),
                "state": status.state,
                "episodes_completed": status.episodes_completed,
                "episodes_total": status.episodes_total,
                "raw_status": status.raw_status,
                "warning": status.warning,
            }
        )
    return rows


def _xp_evidence(ctx, sm, args) -> dict:
    root = ctx.worker or ctx.canonical
    store = RecordStore(Layout(root))
    meta, _ = store.load(args.experiment)
    game = _game()
    with experiment_lock(ctx, args.experiment, "xp evidence"):
        return evidence.collect(
            root,
            sm,
            {**meta, "id": args.experiment},
            binding_evaluate(game, _REPO_ROOT),
            lambda: str(sm.game_version(game.league_id)).strip(),
            runctx.runtime_dir(ctx) / "xp",
            datetime.now(UTC),
        )


def _xp_artifacts(ctx, sm, args) -> dict:
    dest = Path(args.dest)
    downloaded = {}
    for handle in sm.xp_recover(args.experiment):
        if args.batch is not None and handle.batch != args.batch:
            continue
        if args.attempt is not None and handle.attempt != args.attempt:
            continue
        target = dest / handle.request_id
        target.mkdir(parents=True, exist_ok=True)
        downloaded[handle.request_id] = [str(p) for p in sm.xp_artifacts(handle, target)]
    return downloaded


def _upload(ctx, sm, args) -> dict:
    player = os.environ[PLAYER_ENV]
    with experiment_lock(ctx, args.experiment, "upload"):
        version_id = upload(ctx, sm, Path(args.build_dir), args.experiment, player_id=player, name=args.name)
    binding = worker_store(ctx).load(args.experiment)[0]["candidate_binding"]
    return {
        "experiment": args.experiment,
        "version_id": version_id,
        "platform_policy_version_id": binding["platform_policy_version_id"],
    }


def _upload_build(ctx, sm, args) -> dict:
    player = os.environ[PLAYER_ENV]
    version_id, platform_id = upload_build(ctx, sm, Path(args.build_dir), player, args.name)
    return {"version_id": version_id, "platform_policy_version_id": platform_id}


def _leaderboard(ctx, sm, args) -> dict:
    league_id = _league_id()
    player_id = sm.whoami().player_id
    current = sm.leaderboard_champion(player_id, league_id)
    return {
        "league_id": league_id,
        "player_id": player_id,
        "current_champion": current,
        "conflict": promotion.conflict(ctx.canonical, sm, league_id, player_id),
    }


def _latest_league_result(ctx, sm, args) -> dict:
    league_id = _league_id()
    player_id = os.environ[PLAYER_ENV]
    result = sm.latest_league_result(player_id, league_id)
    return {
        "league_id": league_id,
        "player_id": player_id,
        "available": result is not None,
        "result": result,
    }


def _game_version(ctx, sm, args) -> dict:
    league_id = _league_id()
    return {"league_id": league_id, "game_version": str(sm.game_version(league_id)).strip()}


def _standings(ctx, sm, args) -> dict:
    league_id = _league_id()
    return {"league_id": league_id, "rows": sm.standings(league_id)}


LEAGUE_PROGRESS_EVERY = 10  # episodes between progress lines on stderr


def _progress(text: str, started: float) -> None:
    print(f"league-episodes: {text} ({time.monotonic() - started:.0f} s)", file=sys.stderr)


def _league_episodes(ctx, sm, args) -> dict:
    if args.limit < 1:
        raise BadArguments("--limit must be positive")
    game = _game()
    league_id = args.league or game.league_id
    current = str(sm.game_version(league_id)).strip()
    evaluate = binding_evaluate(game, _REPO_ROOT)
    rows = []
    episodes = 0
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="league-episodes-") as workdir:
        # Each episode is evaluated as it arrives, so a binding error shows at once.
        for episode in sm.league_episodes(league_id, args.limit):
            rows += episode_rows(episode, evaluate, Path(workdir), current)
            episodes += 1
            if episodes % LEAGUE_PROGRESS_EVERY == 0:
                _progress(f"{episodes}/{args.limit} episodes, {len(rows)} rows", started)
    _progress(f"done: {episodes} episodes, {len(rows)} rows", started)
    out = Path(args.out)  # written once, complete: a file that exists holds the whole read
    write_text(out, "".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in rows))
    return {
        "league_id": league_id,
        "episodes": episodes,
        "rows": len(rows),
        "out": str(out),
        "current_game_version": current,
        "game_versions": sorted({row.game_version for row in rows}),
    }


# --- parser ---


def _discovery(ctx, sm, args) -> dict:
    # Discovery reads are explicit per operation. They do not load _game(), change
    # identity, or compare a different league with this workspace's promotion state.
    discovery = Discovery(os.environ, _REPO_ROOT)
    match args.command:
        case "discover-leagues":
            return discovery.discover_leagues(args.query, args.offset, args.limit)
        case "league-players":
            return discovery.league_players(args.league, args.query, args.offset, args.limit)
        case "game-support":
            return discovery.game_support(
                args.league, args.document, args.offset, args.limit, args.expected_sha256
            )
        case "player-champion":
            return discovery.leaderboard(args.league, args.player)
        case "player-result":
            return discovery.latest_league_result(args.league, args.player)
    raise BadArguments(f"unknown discovery command: {args.command}")


def setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(subparsers, name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    for name, help_text in (
        ("discover-leagues", "find live leagues and games by name or ID"),
        ("league-players", "find ranked players in an explicit league"),
        ("game-support", "load live documentation and installed research support for a league"),
        ("player-champion", "read an explicit player's champion in an explicit league"),
        ("player-result", "read an explicit player's latest result in an explicit league"),
    ):
        sub = command(commands, name, _discovery, help_text)
        if name != "discover-leagues":
            sub.add_argument("--league", required=True)
        if name in ("discover-leagues", "league-players"):
            sub.add_argument("--query", default="")
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=20)
        if name in ("player-champion", "player-result"):
            sub.add_argument("--player", required=True)
        if name == "game-support":
            sub.add_argument("--document")
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=4000)
            sub.add_argument("--expected-sha256")

    command(commands, "whoami", _whoami, "the credential's identity (must be our Player)")
    command(commands, "credits", _credits, "credit balance, when readable (A10)")

    xp = commands.add_parser("xp", help="experience requests for an experiment")
    xp_commands = xp.add_subparsers(dest="xp_command", metavar="XP_COMMAND", required=True)
    sub = command(
        xp_commands,
        "create",
        _xp_create,
        "launch a batch: one seeded request per (seed, arm), creating only missing ones",
    )
    sub.add_argument("experiment")
    sub.add_argument(
        "--batch", type=int, required=True, help="which slice of design.seeds (1..--batches)"
    )
    sub.add_argument("--attempt", type=int, required=True)
    sub.add_argument(
        "--batches", type=int, default=1, help="slices design.seeds is split into (default 1)"
    )
    sub = command(xp_commands, "status", _xp_status, "status of every request of an experiment")
    sub.add_argument("experiment")
    sub = command(xp_commands, "recover", _xp_recover, "list the experiment's existing requests")
    sub.add_argument("experiment")
    sub = command(
        xp_commands,
        "evidence",
        _xp_evidence,
        "turn finished requests into evidence and episode rows in this worktree",
    )
    sub.add_argument("experiment")

    sub = command(xp_commands, "artifacts", _xp_artifacts, "download and verify results")
    sub.add_argument("experiment")
    sub.add_argument("--dest", required=True)
    sub.add_argument("--batch", type=int)
    sub.add_argument("--attempt", type=int)

    sub = command(commands, "upload", _upload, "upload a build and bind it to the experiment")
    sub.add_argument("experiment")
    sub.add_argument("--build-dir", required=True)
    sub.add_argument(
        "--name", required=True,
        help="Chosen lowercase policy name; scoped to the player. Reuse on retry.",
    )


    sub = command(
        commands,
        "upload-build",
        _upload_build,
        "upload a checked build without an experiment or league submission",
    )
    sub.add_argument("--build-dir", required=True)
    sub.add_argument(
        "--name", required=True,
        help="Chosen lowercase policy name; scoped to the player. Reuse on retry.",
    )

    command(commands, "leaderboard", _leaderboard, "our leaderboard and current champion")
    command(commands, "latest-league-result", _latest_league_result, "our latest live league episode and round (read-only)")
    command(commands, "game-version", _game_version, "the league's current game version")
    command(commands, "standings", _standings, "league standings (read-only)")
    sub = command(
        commands,
        "league-episodes",
        _league_episodes,
        "league episodes as EpisodeRow JSONL for stats ingest --league (read-only)",
    )
    sub.add_argument("--league", help="league id (default: the game's league)")
    sub.add_argument("--limit", type=int, required=True, help="most episodes to read")
    sub.add_argument("--out", required=True, help="EpisodeRow JSONL file to write")


register_group("softmax", setup)
