"""``autoresearch softmax ...``: every platform action a card worker takes (Plan 03 Task 3.4).

Commands (the Softmax backend comes from ``AUTORESEARCH_SOFTMAX``: ``fake`` or the real client;
the game, for its league, from ``AUTORESEARCH_GAME``):

- ``whoami``: the credential's identity; exit 1 (``identity``) unless it is the expected
  (``AUTORESEARCH_PLAYER_ID``) unexpired Player.
- ``credits``: ``{available: true, credits}``, or ``{available: false, reason}`` when the
  balance cannot be read (always, for a Player credential: amendment A10).
- ``xp create <E> --batch N --attempt N [--batches K]``: launch batch N of a paired evaluation:
  one seeded XP request per (seed, arm) in the batch's slice of ``design.seeds`` (see
  ``xp_specs`` for the mapping, A12). It recovers once, backfills any recovered request missing
  from ``experiment.xp_requests``, creates only the missing requests, and appends
  ``{batch, attempt, seed, arm, request_id, policy}`` for each one as soon as it is created (so
  a platform error or crash mid-batch loses no record), then moves ``uploaded|awaiting-credits`` to ``evaluating``. It
  prints ``{created, existing, missing, pairing, credit_denied}``. A credit denial stops the
  batch with exit 0 and ``credit_denied: true``: requests already created stay (and are
  recorded), ``missing`` lists the (seed, arm) pairs not created, the experiment goes
  ``awaiting-credits`` (from ``uploaded|evaluating``) and ``lifecycle.next_launch_after`` is set
  by ``credit_backoff`` (A10); ``lifecycle.credit_denials`` counts consecutive denials. Rerunning
  the same ``--batch``/``--attempt`` later creates only the missing requests.
- ``xp status <E>``, ``xp recover <E>``, ``xp artifacts <E> --dest DIR``: over every request
  tagged for the experiment (``Softmax.xp_recover``); artifacts land in ``DIR/<request id>/``.
- ``upload <E> --build-dir D``: ``softmax_tool.upload``.
- ``submit <E>``: ``softmax_tool.submit``, the only path to a real league submission.
- ``leaderboard``: our leaderboard champion and the confirmed current champion (A9).
- ``game-version``: ``{league_id, game_version}``, the league's current game version
  (``Softmax.game_version``; the version noise records and designs must match, C10).
- ``standings``: ``{league_id, rows}``, the league standings (``Softmax.standings``), read-only.
- ``league-episodes [--league L] --limit N --out rows.jsonl``: up to N completed league
  episodes (``Softmax.league_episodes``), each mapped through the game's evaluate binding
  into EpisodeRows (``softmax_tool.league``), written as JSONL for
  ``stats ingest --league``. Prints ``{league_id, episodes, rows, out, game_versions}``.
  ``--league`` defaults to the game's league.

Record writes go to the worker worktree (``HERMES_KANBAN_WORKSPACE``) only.

Identity is read at call time (C6, C7). Hermes fixes a worker's environment when it spawns
the worker, but the profile ``.env`` can change while the worker runs (for example the operator
sets another player id, and the next preflight pauses the agent). So each command first takes
``IDENTITY_ENV`` (the credential and the expected player id) from ``$HERMES_HOME/.env`` when the
file sets them; a running worker's mutating calls then use the same identity the next
preflight checks.

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
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.games.bindings import binding_evaluate
from autoresearch.games.loader import GameError, load_game
from autoresearch.io.docs import write_text
from autoresearch.queue.select import queue_from_env
from autoresearch.records.store import RecordNotFound, RecordProblems
from autoresearch.runtime.preflight import env_file_value
from autoresearch.runtime.promotion import PromotionError, champion_status
from autoresearch.softmax_tool.client import CreditsUnavailable
from autoresearch.softmax_tool.identity import IdentityError, identity_problems
from autoresearch.softmax_tool.league import episode_rows
from autoresearch.softmax_tool.platform_cli import SoftmaxCliError, ToolMissing
from autoresearch.softmax_tool.protocol import CreditDenied, Refused, XpHandle
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env
from autoresearch.softmax_tool.submit import submit
from autoresearch.softmax_tool.upload import upload, worker_store
from autoresearch.softmax_tool.xp import ARMS, ArtifactsIncomplete
from autoresearch.stats.design import DesignError, EvaluationDesign

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS, EXIT_TOOL_MISSING = 0, 1, 2, 3
_REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ lives at the project root
GAME_ENV = "AUTORESEARCH_GAME"
PLAYER_ENV = "AUTORESEARCH_PLAYER_ID"
IDENTITY_ENV = ("SOFTMAX_PLAYER_CREDENTIAL", PLAYER_ENV)
BACKOFF_BASE = timedelta(minutes=30)
BACKOFF_CAP = timedelta(hours=8)


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


def refresh_identity_env(hermes_home: Path) -> None:
    """Set ``IDENTITY_ENV`` in this process from the profile ``.env`` (module docstring)."""
    for name in IDENTITY_ENV:
        value = env_file_value(Path(hermes_home) / ".env", name)
        if value is not None:
            os.environ[name] = value


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            ctx = runctx.from_env()
        except KeyError as error:
            return _fail("bad_arguments", f"{error.args[0]} is not set", EXIT_BAD_ARGS)
        refresh_identity_env(ctx.hermes_home)
        try:
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


def _game():
    name = os.environ.get(GAME_ENV)
    if not name:
        raise BadArguments(f"{GAME_ENV} is not set")
    return load_game(_REPO_ROOT, name)


def _league_id() -> str:
    return _game().league_id


# --- XP mapping (pure) ---


def credit_backoff(denials: int) -> timedelta:
    """Wait before relaunching after the ``denials``-th consecutive credit denial (A10):
    30 min, doubling, capped at 8 h."""
    return min(BACKOFF_BASE * 2 ** (denials - 1), BACKOFF_CAP)


def _opponent(entry: str) -> dict:
    player = {"random": True} if entry == "random" else {"policy_ref": entry}
    return {"slot": -1, "player": player}


def _roster(design: EvaluationDesign, policy: str) -> list[dict]:
    """Our policy in each ``design.seats`` slot, then ``design.opponents`` in order in the
    remaining slots (ascending, skipping our seats). Fixed slots keep a pair's seating
    identical; with ``slot: -1`` the platform would rotate open seats per episode."""
    roster = [{"slot": seat, "player": {"policy_ref": policy}} for seat in design.seats]
    free_slots = (slot for slot in itertools.count() if slot not in design.seats)
    for entry, slot in zip(design.opponents, free_slots, strict=False):
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
    - Opponents fill the other slots in order as fixed seats: a policy ref is identical across
      the pair; ``"random"`` is sampled per request by the platform, so pairing is then
      seed-only (``EvaluationDesign.pairing``). The roster must fill the game's seat count.
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
                "roster": _roster(parsed, policies[arm]),
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
        "expires_at": identity.expires_at.isoformat(),
        "problems": problems,
    }


def _credits(ctx, sm, args) -> dict:
    try:
        return {"available": True, "credits": sm.credits()}
    except CreditsUnavailable as error:
        return {"available": False, "reason": str(error)}


def _credit_denied(store, experiment: str, meta: dict, reason: str) -> dict:
    lifecycle = dict(meta.get("lifecycle") or {})
    denials = int(lifecycle.get("credit_denials", 0)) + 1
    after = datetime.now(UTC) + credit_backoff(denials)
    next_launch_after = after.isoformat(timespec="seconds")
    patch: dict = {
        "lifecycle": {
            **lifecycle,
            "credit_denials": denials,
            "next_launch_after": next_launch_after,
        }
    }
    if meta["status"] in ("uploaded", "evaluating"):
        patch["status"] = "awaiting-credits"
    store.update(experiment, patch)
    return {
        "credit_denied": True,
        "reason": reason,
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


def _xp_create(ctx, sm, args) -> dict:
    store = worker_store(ctx)
    experiment = args.experiment
    meta, _ = store.load(experiment)
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
    existing = {
        (handle.seed, handle.arm): handle
        for handle in recovered
        if (handle.batch, handle.attempt) == (args.batch, args.attempt)
    }
    created: list[dict] = []
    denial: CreditDenied | None = None
    for seed, arm, spec in planned:
        if (seed, arm) in existing:
            continue
        result = sm.xp_create(
            experiment, args.batch, args.attempt, spec, seed=seed, arm=arm, known=recovered
        )
        if isinstance(result, CreditDenied):
            denial = result
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
    if denial is not None:
        return {**report, **_credit_denied(store, experiment, meta, denial.reason)}

    patch: dict = {}
    if meta["status"] in ("uploaded", "awaiting-credits"):
        patch["status"] = "evaluating"
    lifecycle = meta.get("lifecycle") or {}
    if lifecycle.get("credit_denials") or lifecycle.get("next_launch_after"):
        patch["lifecycle"] = {**lifecycle, "credit_denials": 0, "next_launch_after": None}
    if patch:
        store.update(experiment, patch)
    return {**report, "credit_denied": False}


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
    return submit(
        ctx, queue, sm, args.experiment, _league_id(), player, candidate=args.candidate_path
    )


def _leaderboard(ctx, sm, args) -> dict:
    league_id = _league_id()
    player_id = sm.whoami().player_id
    on_board = sm.leaderboard_champion(player_id, league_id)
    current, note = champion_status(ctx, sm, league_id)
    return {
        "league_id": league_id,
        "player_id": player_id,
        "leaderboard_champion": on_board,
        "current_champion": current,
        "note": note,
    }


def _game_version(ctx, sm, args) -> dict:
    league_id = _league_id()
    return {"league_id": league_id, "game_version": str(sm.game_version(league_id)).strip()}


def _standings(ctx, sm, args) -> dict:
    league_id = _league_id()
    return {"league_id": league_id, "rows": sm.standings(league_id)}


def _league_episodes(ctx, sm, args) -> dict:
    if args.limit < 1:
        raise BadArguments("--limit must be positive")
    game = _game()
    league_id = args.league or game.league_id
    current = str(sm.game_version(league_id)).strip()
    evaluate = binding_evaluate(game, _REPO_ROOT)
    episodes = sm.league_episodes(league_id, args.limit)
    rows = []
    with tempfile.TemporaryDirectory(prefix="league-episodes-") as workdir:
        for episode in episodes:
            rows += episode_rows(episode, evaluate, Path(workdir), current)
    out = Path(args.out)
    write_text(out, "".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in rows))
    return {
        "league_id": league_id,
        "episodes": len(episodes),
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
