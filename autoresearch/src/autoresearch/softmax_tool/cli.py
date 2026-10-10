"""``autoresearch softmax ...``: every platform action a card worker takes (Plan 03 Task 3.4).

Commands (the Softmax backend comes from ``AUTORESEARCH_SOFTMAX``: ``fake`` or the real client;
the game, for its league, from ``AUTORESEARCH_GAME``):

- ``whoami``: the credential's identity (``expires_at`` is null for a ``cga_`` agent token,
  which does not expire); exit 1 (``identity``) unless it is the expected
  (``AUTORESEARCH_PLAYER_ID``) unexpired Player.
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
  league_not_chosen}``. A credit denial stops the batch
  with exit 0 and ``credit_denied: true``: requests already created stay (and are
  recorded), ``missing`` lists the (seed, arm) pairs not created, the experiment goes
  ``awaiting-credits`` (from ``uploaded|evaluating``) and ``lifecycle.next_launch_after`` is set
  by ``credit_backoff`` (A10; base 30 minutes, or ``AUTORESEARCH_CREDIT_BACKOFF_MINUTES`` from
  the worker's environment, read before any request is created; it is read in worker terminals
  only, which see the whole profile ``.env``, so it is deliberately not on
  ``terminal.env_passthrough``: a cron job would not get it); ``lifecycle.credit_denials``
  counts consecutive denials and ``lifecycle.credits_at_denial`` is the balance read right
  after the denial (null when unreadable). xp-poll relaunches when the balance covers the
  remaining requests and is above that value, else after the backoff
  (``runtime.xp_poll.relaunch_due``). The coach's refusals (Softmax hosting) end with exit 0
  too, and the experiment goes ``awaiting-permit`` (from ``uploaded|evaluating|awaiting-credits``)
  with ``lifecycle.permit_wait`` = ``{kind, batch, attempt, batches, league_id, reason, asks,
  refusals, since, retry_minutes, not_before}``. A permit refusal (the IDE Controls ask before XP
  requests, ``permit_required: true``) does not stop the batch: every request is tried, so one
  run files every ask, and ``asks`` lists one ``{request, seed, arm, ask, state, reason}`` per
  refused request (``state``: ``waiting``, ``declined`` or ``over_cap``, from the server's
  reason; ``not_before`` is the next UTC day when one is over today's cap). A league refusal
  (no league or another league chosen, ``league_not_chosen: true``) stops the batch. xp-poll
  reads each ask's answer and relaunches once all are yes (``runtime.xp_poll``); the relaunch
  sends the same body and idempotency key, so the server decides it against the same ask.
  ``lifecycle.next_launch_after`` is the fallback for when the answers cannot be read (and for
  a league refusal): the credit backoff, counted by ``refusals`` of the same asks only. A
  credit denial of an ``awaiting-permit`` experiment moves it to ``awaiting-credits``. A
  ``stopped`` experiment (the coach declined it) is refused (``experiment_closed``) before
  anything is created.
  Rerunning the same ``--batch``/``--attempt`` later creates only the missing requests. A new
  attempt (one with no request yet in the batch) is a retry, and
  the only reason to retry is a failed or cancelled request, so it is refused unless it is
  the next number (``bad_attempt``), the batch's latest attempt has every request
  (``attempt_incomplete``: rerun that attempt), and at least one of those requests is
  ``failed`` (``attempt_not_needed``; read with ``xp_status``). So no relaunch, however many
  run one after another, repeats a healthy attempt. Two relaunchers at the same moment run
  one after the other (the experiment lock, below), so the second sees what the first
  created. Not covered: a ``--batches`` change between runs.
- ``xp status <E>``, ``xp recover <E>``, ``xp artifacts <E> --dest DIR``: over every request
  tagged for the experiment (``Softmax.xp_recover``); artifacts land in ``DIR/<request id>/``.
- ``upload <E> --build-dir D``: ``softmax_tool.upload``.
- ``submit <E>``: ``softmax_tool.submit``, the only path to a real league submission of an
  experiment's candidate. The coach's refusals are ``permit_required`` and
  ``league_not_chosen`` (exit 1): the promotion stays at ``intent`` and the runtime asks the
  coach and retries the same submit (``runtime.promotion``, "Waiting for the coach").
- ``bootstrap-champion --build-dir D``: ``softmax_tool.submit.bootstrap_champion``, the
  first champion on a fresh install (A14): with no champion (the league shows no policy
  competing for our player, A9), upload the game's starter policy build and promote it through the C8
  promotion as a ``bootstrap`` promotion (no experiment). Prints ``{version_id,
  platform_policy_version_id, promotion_id, submission_id, champion_path}``. Refusals:
  ``champion_exists``, ``champion_conflict``, ``promotion_in_flight``, ``not_shipped_strategy``,
  ``promotion_refused``, ``permit_required``, ``league_not_chosen`` (as for ``submit``; the
  upload stands), ``identity`` and the build reader's
  ``gates_failed``/``bad_build``/``artifact_mismatch``.
- ``leaderboard``: ``{league_id, player_id, leaderboard_champion, current_champion, note,
  conflict}``. The champion is the policy the league shows competing for our player (A9);
  ``leaderboard_champion`` and ``current_champion`` are both that policy (null: none, and
  ``note`` says so). ``conflict`` is null, or ``{expected, shown, detail, waited_out}`` when
  what the loop expects differs from the league (``promotion.league_check``; queue-keeper
  pauses the agent for it). ``submission`` is null, or ``{stage, is_champion, detail}``: where
  the latest promotion's submission stands when the league does not show it (``pending``,
  ``competing``, ``rejected``, ``unknown``). A malformed grace setting warns on stderr.
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

Experiment lock. ``xp create``, ``upload`` and ``submit`` hold a per-experiment lock for the
whole command: ``fcntl.flock`` on ``.supervisor/runtime/locks/xp-<E>.lock`` in the canonical
workspace, shared by every worker on the host. The holder writes ``{task_id, run_id, pid,
command, started_at}`` into the file while it holds the lock and empties it before release. A
second command for the same experiment waits up to ``LOCK_WAIT_S`` (60 s; below Hermes'
180 s foreground terminal timeout), then refuses with ``busy`` and does nothing; ``details``
carries ``holder`` and ``own_run`` (the holder is this card's own run, for example an earlier
call still running in the background: wait for it, do not give up the card). The kernel
releases the lock when the process exits, so a crashed worker never leaves it held. Do not
delete ``runtime/locks/`` while workers run: a new file would let a second holder in. The
experiment argument must be an ``E-`` id (``bad_arguments`` otherwise), because it names the
lock file.

Identity is the spawn-time one (C6, C7). Each command uses the credential and expected player
id in its own environment (what Hermes gave the worker when it started) for ``whoami`` and the
mutating call. The profile ``.env`` is read only to refuse: when it sets ``IDENTITY_ENV`` names
to other values (the operator changed the player since this worker started, and the next
preflight pauses the agent), every command fails with ``identity``. The ``.env`` is never a
source of identity, because a worker could write it.

Prints one JSON envelope: ``{"ok": true, "data": ...}`` or ``{"ok": false, "error": {"reason",
"message", "details"?}}``. Exit codes: 0 done (including a credit denial), 1 refused or failed
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

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.games.bindings import binding_evaluate
from autoresearch.games.loader import GameError, load_game
from autoresearch.ids import parse_id
from autoresearch.io.docs import write_text
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.queue.select import queue_from_env
from autoresearch.records.store import RecordNotFound, RecordProblems
from autoresearch.runtime.preflight import env_file_value
from autoresearch.runtime.promotion import (
    DEFAULT_CHAMPION_GRACE,
    PromotionError,
    baseline_problem,
    champion_grace,
    champion_status,
    league_check,
)
from autoresearch.softmax_tool.identity import IdentityError, identity_problems
from autoresearch.softmax_tool.league import episode_rows
from autoresearch.softmax_tool.platform_cli import SoftmaxCliError, ToolMissing
from autoresearch.softmax_tool.protocol import (
    PERMIT_OVER_CAP,
    CreditDenied,
    LeagueNotChosen,
    PermitRequired,
    Refused,
    XpHandle,
    read_credits,
)
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env
from autoresearch.softmax_tool.submit import bootstrap_champion, submit
from autoresearch.softmax_tool.upload import upload, worker_store
from autoresearch.softmax_tool.xp import ARMS, ArtifactsIncomplete, tag
from autoresearch.stats.design import DesignError, EvaluationDesign

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS, EXIT_TOOL_MISSING = 0, 1, 2, 3
_REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ lives at the project root
GAME_ENV = "AUTORESEARCH_GAME"
PLAYER_ENV = "AUTORESEARCH_PLAYER_ID"
IDENTITY_ENV = ("SOFTMAX_PLAYER_CREDENTIAL", PLAYER_ENV)
BACKOFF_BASE = timedelta(minutes=30)
BACKOFF_ENV = "AUTORESEARCH_CREDIT_BACKOFF_MINUTES"
BACKOFF_CAP = timedelta(hours=8)
LOCK_WAIT_S = 60.0  # how long a command waits for another process's experiment lock
# Statuses whose evaluation is over: ``xp create`` refuses them (``experiment_closed``).
CLOSED_EXPERIMENT = ("stopped",)
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
        except (BadArguments, GameError) as error:
            return _fail("bad_arguments", str(error), EXIT_BAD_ARGS)
        except Refused as error:
            return _fail(error.reason, str(error), EXIT_FAILED, error.details)
        except IdentityError as error:
            return _fail("identity", str(error), EXIT_FAILED)
        except ToolMissing as error:
            return _fail("tool_missing", str(error), EXIT_TOOL_MISSING)
        except ArtifactsIncomplete as error:
            return _fail("artifacts_incomplete", str(error), EXIT_FAILED)
        except PromotionError as error:
            return _fail("promotion", str(error), EXIT_FAILED)
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
    path = runctx.runtime_dir(ctx) / "locks" / f"xp-{experiment}.lock"
    with ExitStack() as held:
        try:  # only the acquisition: a LockTimeout from the command itself is not "busy"
            handle = held.enter_context(file_lock(path, timeout_s=LOCK_WAIT_S))
        except LockTimeout:
            raise _busy(ctx, experiment, _read_holder(path)) from None
        record = {
            "task_id": ctx.card_id,
            "run_id": ctx.run_id,
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
    own_run = bool(
        holder and ctx.card_id and holder.get("task_id") == ctx.card_id
        and str(holder.get("run_id")) == str(ctx.run_id)
    )
    if holder is None:
        who = "another process (holder not recorded yet)"
    else:
        who = (
            f"card {holder.get('task_id')} run {holder.get('run_id')} "
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
            'See the running cards: "$AUTORESEARCH_CLI" queue list --state claimed. Do not '
            "duplicate that card's work; rerun later only if it is still needed"
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


def credit_backoff(denials: int, base: timedelta = BACKOFF_BASE) -> timedelta:
    """Wait before relaunching after the ``denials``-th consecutive credit denial (A10):
    ``base`` (30 min), doubling, capped at 8 h."""
    return min(base * 2 ** (denials - 1), BACKOFF_CAP)


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


def _credit_denied(
    store, experiment: str, meta: dict, reason: str, base: timedelta, credits: float | None
) -> dict:
    lifecycle = dict(meta.get("lifecycle") or {})
    denials = int(lifecycle.get("credit_denials", 0)) + 1
    after = datetime.now(UTC) + credit_backoff(denials, base)
    next_launch_after = after.isoformat(timespec="seconds")
    patch: dict = {
        "lifecycle": {
            **lifecycle,
            "credit_denials": denials,
            "next_launch_after": next_launch_after,
            "credits_at_denial": credits,
        }
    }
    if meta["status"] in ("uploaded", "evaluating", "awaiting-permit"):
        patch["status"] = "awaiting-credits"
    store.update(experiment, patch)
    return {
        **NOT_REFUSED,
        "credit_denied": True,
        "reason": reason,
        "next_launch_after": next_launch_after,
        "status": patch.get("status", meta["status"]),
    }


def _next_utc_day(now: datetime) -> datetime:
    """The start of the next UTC day: when an over-the-cap spend can be covered again."""
    return datetime.combine(now.astimezone(UTC).date() + timedelta(days=1), dtime.min, UTC)


def _awaiting_coach(
    store,
    experiment: str,
    meta: dict,
    args,
    refusal: LeagueNotChosen | None,
    asks: list[dict],
    league_id: str,
    base: timedelta,
) -> dict:
    """The coach refused the batch: the league choice (``refusal``), or one permit ask per
    refused request (``asks``). ``awaiting-permit`` with the wait recorded (module docstring,
    ``xp create``)."""
    lifecycle = dict(meta.get("lifecycle") or {})
    now = datetime.now(UTC)
    kind = "league" if refusal is not None else "permit"
    previous = lifecycle.get("permit_wait") or {}
    keys = sorted(entry["ask"] or "" for entry in asks)
    same = previous.get("kind") == kind and sorted(
        entry.get("ask") or "" for entry in previous.get("asks") or []
    ) == keys
    # The fallback backoff counts the refusals of these asks only (a new wait starts over).
    refusals = int(previous.get("refusals", 0)) + 1 if same else 1
    since = previous.get("since") if same and previous.get("since") else now.isoformat()
    after = now + credit_backoff(refusals, base)
    wait = {
        "kind": kind,
        "batch": args.batch,
        "attempt": args.attempt,
        "batches": args.batches,
        "league_id": league_id,
        "reason": refusal.reason if refusal is not None else None,
        "asks": asks,
        "refusals": refusals,
        "since": since,  # when the coach was first asked these asks (the reminder clock)
        "retry_minutes": int(base.total_seconds() // 60),
        "not_before": None,
    }
    if any(entry["state"] == PERMIT_OVER_CAP for entry in asks):
        wait["not_before"] = _next_utc_day(now).isoformat(timespec="seconds")
        after = max(after, _next_utc_day(now))
    next_launch_after = after.isoformat(timespec="seconds")
    patch: dict = {
        "lifecycle": {**lifecycle, "next_launch_after": next_launch_after, "permit_wait": wait}
    }
    if meta["status"] in ("uploaded", "evaluating", "awaiting-credits"):
        patch["status"] = "awaiting-permit"
    store.update(experiment, patch)
    return {
        **NOT_REFUSED,
        "permit_required": kind == "permit",
        "league_not_chosen": kind == "league",
        "reason": refusal.reason if refusal is not None else asks[0]["reason"],
        "permit_asks": asks,
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
    if not attempts or attempt in attempts:
        return
    latest = max(attempts)
    if attempt != latest + 1:
        raise Refused(
            "bad_attempt",
            f"batch {batch} has attempts {sorted(attempts)}; a new attempt must be {latest + 1}",
        )
    have = {(h.seed, h.arm) for h in recovered if (h.batch, h.attempt) == (batch, latest)}
    missing = [(seed, arm) for seed, arm, _ in planned if (seed, arm) not in have]
    if missing:
        raise Refused(
            "attempt_incomplete",
            f"batch {batch} attempt {latest} still lacks {len(missing)} request(s); rerun "
            f"xp create with --attempt {latest} to create only those (a new attempt would "
            f"repeat every request)",
        )
    latest_handles = [h for h in recovered if (h.batch, h.attempt) == (batch, latest)]
    if not any(sm.xp_status(handle).state == "failed" for handle in latest_handles):
        raise Refused(
            "attempt_not_needed",
            f"batch {batch} attempt {latest} has every request and none failed; a new attempt "
            f"would repeat them all. Nothing to relaunch: wait for the review card",
        )


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
    _record_xp(store, experiment, [_xp_entry(h, policies.get(h.arm)) for h in recovered])
    check_new_attempt(sm, recovered, planned, args.batch, args.attempt)
    existing = {
        (handle.seed, handle.arm): handle
        for handle in recovered
        if (handle.batch, handle.attempt) == (args.batch, args.attempt)
    }
    if any((seed, arm) not in existing for seed, arm, _ in planned):
        # Identity first: the champion read is per player, so a wrong credential would
        # otherwise show up as "no champion" (the create re-checks it anyway, C6).
        expected = os.environ.get(PLAYER_ENV, "")
        problems = identity_problems(sm.whoami(), expected, datetime.now(UTC))
        if problems:
            raise IdentityError("; ".join(problems))
        problem = baseline_problem(ctx, sm, _league_id(), design["baseline_version"])
        if problem is not None:
            raise Refused(*problem)
    created: list[dict] = []
    denial: CreditDenied | LeagueNotChosen | None = None
    asks: list[dict] = []  # one per request refused for a permit; the batch goes on
    for seed, arm, spec in planned:
        if (seed, arm) in existing:
            continue
        result = sm.xp_create(
            experiment, args.batch, args.attempt, spec, seed=seed, arm=arm, known=recovered
        )
        if isinstance(result, PermitRequired):
            # Each request has its own ask: file them all in this run, so the coach sees the
            # whole batch at once. A refused request holds no credits.
            request = tag(experiment, args.batch, args.attempt, seed, arm)
            asks.append(
                {
                    "request": request,
                    "seed": seed,
                    "arm": arm,
                    "ask": result.ask,
                    "state": result.state,
                    "reason": result.reason,
                }
            )
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
    if isinstance(denial, CreditDenied):
        credits = read_credits(sm)
        return {**report, **_credit_denied(store, experiment, meta, denial.reason, base, credits)}
    if denial is not None or asks:
        waiting = _awaiting_coach(store, experiment, meta, args, denial, asks, _league_id(), base)
        return {**report, **waiting}

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
    player = os.environ.get(PLAYER_ENV) or None
    with experiment_lock(ctx, args.experiment, "upload"):
        version_id = upload(ctx, sm, Path(args.build_dir), args.experiment, player_id=player)
    binding = worker_store(ctx).load(args.experiment)[0]["candidate_binding"]
    return {
        "experiment": args.experiment,
        "version_id": version_id,
        "platform_policy_version_id": binding["platform_policy_version_id"],
    }


def _submit(ctx, sm, args) -> dict:
    queue = queue_from_env(os.environ, ctx)
    player = os.environ.get(PLAYER_ENV, "")
    league_id = _league_id()
    with experiment_lock(ctx, args.experiment, "submit"):
        return submit(
            ctx, queue, sm, args.experiment, league_id, player, candidate=args.candidate_path
        )


def _bootstrap_champion(ctx, sm, args) -> dict:
    queue = queue_from_env(os.environ, ctx)
    player = os.environ.get(PLAYER_ENV, "")
    return bootstrap_champion(ctx, queue, sm, Path(args.build_dir), _league_id(), player)


def _leaderboard(ctx, sm, args) -> dict:
    league_id = _league_id()
    player_id = sm.whoami().player_id
    current, note = champion_status(ctx, sm, league_id)
    try:
        grace = champion_grace(os.environ)
    except ValueError as error:  # a read-only command: warn and use the default
        print(f"warning: {error}; using {DEFAULT_CHAMPION_GRACE}", file=sys.stderr)
        grace = DEFAULT_CHAMPION_GRACE
    check = league_check(ctx, sm, league_id, datetime.now(UTC), grace)
    return {
        "league_id": league_id,
        "player_id": player_id,
        "leaderboard_champion": current,
        "current_champion": current,
        "note": note,
        "conflict": None if check.conflict is None else asdict(check.conflict),
        "submission": None if check.submission is None else asdict(check.submission),
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


def setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(subparsers, name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

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
    sub = command(xp_commands, "artifacts", _xp_artifacts, "download and verify results")
    sub.add_argument("experiment")
    sub.add_argument("--dest", required=True)
    sub.add_argument("--batch", type=int)
    sub.add_argument("--attempt", type=int)

    sub = command(commands, "upload", _upload, "upload a build and bind it to the experiment")
    sub.add_argument("experiment")
    sub.add_argument("--build-dir", required=True)

    sub = command(commands, "submit", _submit, "submit the promoted candidate (guarded, C8)")
    sub.add_argument("experiment")
    sub.add_argument(
        "--candidate-path",
        help="candidate STRATEGY path on this card's branch "
        "(default strategy/candidates/<E>/STRATEGY.md)",
    )

    sub = command(
        commands,
        "bootstrap-champion",
        _bootstrap_champion,
        "fresh install: upload the game's starter policy and promote it as the first champion",
    )
    sub.add_argument("--build-dir", required=True)

    command(commands, "leaderboard", _leaderboard, "our leaderboard and current champion")
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
