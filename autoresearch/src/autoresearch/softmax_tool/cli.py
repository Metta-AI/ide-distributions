"""``autoresearch softmax ...``: every platform action a card worker takes (Plan 03 Task 3.4).

Commands (the Softmax backend comes from ``AUTORESEARCH_SOFTMAX``: ``fake`` or the real client;
the game, for its league, from ``AUTORESEARCH_GAME``):

- ``whoami``: the credential's identity; exit 1 (``identity``) unless it is the expected
  (``AUTORESEARCH_PLAYER_ID``) unexpired Player.
- ``credits``: ``{available: true, credits}``, or ``{available: false, reason}`` when the
  balance cannot be read (always, for a Player credential: amendment A10).
- ``xp create <E> --batch N --attempt N [--batches K]``: launch one XP request (see ``xp_spec``
  for the mapping from the frozen design). Success appends ``{batch, attempt, request_id, side,
  policy}`` to ``experiment.xp_requests`` (once) and moves ``uploaded|awaiting-credits`` to
  ``evaluating``. A credit denial is exit 0 with ``{credit_denied: true}``: the experiment goes
  ``awaiting-credits`` (from ``uploaded|evaluating``) and ``lifecycle.next_launch_after`` is set
  by ``credit_backoff`` (A10); ``lifecycle.credit_denials`` counts consecutive denials.
- ``xp status <E>``, ``xp recover <E>``, ``xp artifacts <E> --dest DIR``: over every request
  tagged for the experiment (``Softmax.xp_recover``); artifacts land in ``DIR/<request id>/``.
- ``upload <E> --build-dir D``: ``softmax_tool.upload``.
- ``submit <E>``: ``softmax_tool.submit``, the only path to a real league submission.
- ``leaderboard``: our leaderboard champion and the confirmed current champion (A9).

Record writes go to the worker worktree (``HERMES_KANBAN_WORKSPACE``) only.

Prints one JSON envelope: ``{"ok": true, "data": ...}`` or ``{"ok": false, "error": {"reason",
"message", "details"?}}``. Exit codes: 0 done (including a credit denial), 1 refused or failed
(``identity``, a guard's reason, ``platform``, ...), 2 bad arguments or no profile context,
3 the pinned softmax/coworld executable is missing.
"""

import argparse
import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.games.loader import GameError, load_game
from autoresearch.queue.select import queue_from_env
from autoresearch.records.store import RecordNotFound, RecordProblems
from autoresearch.runtime.promotion import PromotionError, champion_status
from autoresearch.softmax_tool.client import CreditsUnavailable
from autoresearch.softmax_tool.identity import IdentityError, identity_problems
from autoresearch.softmax_tool.platform_cli import SoftmaxCliError, ToolMissing
from autoresearch.softmax_tool.protocol import CreditDenied, Refused, XpHandle
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env
from autoresearch.softmax_tool.submit import submit
from autoresearch.softmax_tool.upload import upload, worker_store
from autoresearch.softmax_tool.xp import ArtifactsIncomplete
from autoresearch.stats.design import DesignError, EvaluationDesign

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS, EXIT_TOOL_MISSING = 0, 1, 2, 3
_REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ lives at the project root
GAME_ENV = "AUTORESEARCH_GAME"
PLAYER_ENV = "AUTORESEARCH_PLAYER_ID"
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


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            ctx = runctx.from_env()
        except KeyError as error:
            return _fail("bad_arguments", f"{error.args[0]} is not set", EXIT_BAD_ARGS)
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


def _league_id() -> str:
    name = os.environ.get(GAME_ENV)
    if not name:
        raise BadArguments(f"{GAME_ENV} is not set")
    return load_game(_REPO_ROOT, name).league_id


# --- XP mapping (pure) ---


def credit_backoff(denials: int) -> timedelta:
    """Wait before relaunching after the ``denials``-th consecutive credit denial (A10):
    30 min, doubling, capped at 8 h."""
    return min(BACKOFF_BASE * 2 ** (denials - 1), BACKOFF_CAP)


def _opponent(entry: str) -> dict:
    player = {"random": True} if entry == "random" else {"policy_ref": entry}
    return {"slot": -1, "player": player}


def xp_spec(
    design: dict, binding: dict, league_id: str, batch: int, batches: int
) -> tuple[dict, str]:
    """(XP spec, side) for request ``batch`` of an evaluation split into ``batches`` pairs.

    Mapping from the frozen evaluation design:

    - A comparison needs episodes of both policies, and one XP request has one roster, so the
      requests come in pairs: an odd ``batch`` plays the candidate (the binding's platform id),
      the next even ``batch`` plays ``design.baseline_version``. Pair ``p`` is batches
      ``2p-1`` and ``2p``. Each tag ``ar:<E>:b<batch>:a<attempt>`` stays unique.
    - ``num_episodes`` = ceil(``n_min`` / ``batches``), so ``batches`` pairs give at least
      ``n_min`` episodes per side.
    - Roster: our policy in each ``design.seats`` slot; then one entry per
      ``design.opponents`` item with ``slot: -1`` (the platform assigns the slot): ``"random"``
      is a random league player, anything else a policy ref.
    - ``design.seeds`` cannot be sent: coworld 0.1.57 XP requests take no seeds.
    """
    if batch < 1 or batches < 1:
        raise BadArguments("--batch and --batches must be at least 1")
    parsed = EvaluationDesign.from_dict(design)
    side = "candidate" if batch % 2 == 1 else "baseline"
    policy = (
        binding["platform_policy_version_id"] if side == "candidate" else (parsed.baseline_version)
    )
    roster = [{"slot": seat, "player": {"policy_ref": policy}} for seat in parsed.seats]
    roster += [_opponent(entry) for entry in parsed.opponents]
    spec = {
        "league_id": league_id,
        "num_episodes": math.ceil(parsed.n_min / batches),
        "roster": roster,
    }
    return spec, side


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


def _xp_create(ctx, sm, args) -> dict:
    store = worker_store(ctx)
    experiment = args.experiment
    meta, _ = store.load(experiment)
    binding = meta.get("candidate_binding")
    if not binding:
        raise Refused("no_binding", f"{experiment} has no candidate_binding; upload first")
    spec, side = xp_spec(meta["evaluation_design"], binding, _league_id(), args.batch, args.batches)
    result = sm.xp_create(experiment, args.batch, args.attempt, spec)
    if isinstance(result, CreditDenied):
        return _credit_denied(store, experiment, meta, result.reason)
    policy = spec["roster"][0]["player"]["policy_ref"] if spec["roster"] else None
    entry = {
        "batch": args.batch,
        "attempt": args.attempt,
        "request_id": result.request_id,
        "side": side,
        "policy": policy,
    }
    requests = meta.get("xp_requests") or []
    patch: dict = {}
    if not any((r.get("batch"), r.get("attempt")) == (args.batch, args.attempt) for r in requests):
        patch["xp_requests"] = [*requests, entry]
    if meta["status"] in ("uploaded", "awaiting-credits"):
        patch["status"] = "evaluating"
    lifecycle = meta.get("lifecycle") or {}
    if lifecycle.get("credit_denials") or lifecycle.get("next_launch_after"):
        patch["lifecycle"] = {**lifecycle, "credit_denials": 0, "next_launch_after": None}
    if patch:
        store.update(experiment, patch)
    return {
        **entry,
        "experiment": experiment,
        "num_episodes": spec["num_episodes"],
        "credit_denied": False,
    }


def _handle_dict(handle: XpHandle) -> dict:
    return {
        "request_id": handle.request_id,
        "experiment": handle.experiment,
        "batch": handle.batch,
        "attempt": handle.attempt,
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
    sub = command(xp_commands, "create", _xp_create, "launch one XP request (idempotent)")
    sub.add_argument("experiment")
    sub.add_argument("--batch", type=int, required=True, help="odd: candidate, even: baseline")
    sub.add_argument("--attempt", type=int, required=True)
    sub.add_argument("--batches", type=int, default=1, help="pairs the evaluation is split into")
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


register_group("softmax", setup)
