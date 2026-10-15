"""``autoresearch promotion ...``: the trusted promotion steps a card worker runs (C8).

- ``promotion status``: the current promotion record (``null`` when there is none).
- ``promotion adopt <P-id>``: the promotion-recovery card takes over a ``submitted``
  promotion (writes the ledger under the canonical lock).
- ``promotion stage-champion <P-id>``: copy the candidate STRATEGY at ``candidate_sha`` to
  the champion path in this card's worktree; the holder task only (adopt first).
- ``promotion accept-champion``: operator only (refused in a card context, and blocked for
  agents by the plugin policy and the approvals deny list). Resolve a champion conflict (A9) by accepting
  the policy the league shows now (possibly none) as what the loop expects
  (``promotion.accept_champion``; the game from ``AUTORESEARCH_GAME``, the Softmax backend from
  ``AUTORESEARCH_SOFTMAX``). queue-keeper resumes the agent on its next clean check.

Prints one JSON envelope like ``card``: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"message": ...}}``. Exit codes: 0 done, 1 refused (PromotionError)
or a git/filesystem failure, 2 bad arguments or no runtime context.
"""

import argparse
import json
import os

from ide_research_core import runctx
from ide_research_core.cli import register_group
from ide_research_core.games.loader import GameError, load_game
from ide_research_core.io import gitops
from ide_research_core.runtime.bootstrap import REPO_ROOT
from ide_research_core.runtime.promotion import (
    PromotionError,
    accept_champion,
    adopt,
    load,
    stage_champion_for,
)
from ide_research_core.softmax_tool.protocol import from_env as softmax_from_env

EXIT_OK, EXIT_REFUSED, EXIT_BAD_ARGS = 0, 1, 2


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _fail(message: str, code: int) -> int:
    _emit({"ok": False, "error": {"message": message}})
    return code


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            ctx = runctx.from_env()
        except KeyError as error:
            return _fail(f"{error.args[0]} is not set", EXIT_BAD_ARGS)
        try:
            data = handler(ctx, args)
        except PromotionError as error:
            return _fail(str(error), EXIT_REFUSED)
        except (gitops.GitError, OSError) as error:  # e.g. fetching the candidate branch
            return _fail(f"{type(error).__name__}: {error}", EXIT_REFUSED)
        except (RuntimeError, ValueError, GameError) as error:  # accept-champion: Softmax, game
            return _fail(f"{type(error).__name__}: {error}", EXIT_REFUSED)
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


def _status(ctx, args) -> dict | None:
    current = load(ctx.canonical)
    return None if current is None else current.to_dict()


def _adopt(ctx, args) -> dict:
    return adopt(ctx, args.promotion_id).to_dict()


def _stage_champion(ctx, args) -> dict:
    return {"path": str(stage_champion_for(ctx, args.promotion_id))}


def _accept_champion(ctx, args) -> dict:
    if ctx.card_id or ctx.worker or os.environ.get(runctx.ENV_WORKER):
        raise PromotionError(
            "accept-champion is for a human operator, not a card: a champion conflict pauses "
            "the agent until a person resolves it (A9)"
        )
    game = load_game(REPO_ROOT, os.environ.get("AUTORESEARCH_GAME") or "gota")
    sm = softmax_from_env(os.environ, ctx.canonical)
    return accept_champion(ctx, sm, game.league_id)


def _setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    sub = commands.add_parser("status", help="show the current promotion")
    sub.set_defaults(func=_run(_status))
    sub = commands.add_parser("adopt", help="recovery card: take over a submitted promotion")
    sub.add_argument("promotion_id")
    sub.set_defaults(func=_run(_adopt))
    sub = commands.add_parser(
        "stage-champion", help="copy the candidate STRATEGY to the champion path"
    )
    sub.add_argument("promotion_id")
    sub.set_defaults(func=_run(_stage_champion))
    sub = commands.add_parser(
        "accept-champion", help="operator: accept the league's champion after a conflict (A9)"
    )
    sub.set_defaults(func=_run(_accept_champion))


register_group("promotion", _setup)
