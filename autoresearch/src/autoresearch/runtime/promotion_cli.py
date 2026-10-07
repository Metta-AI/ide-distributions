"""``autoresearch promotion ...``: the trusted promotion steps a card worker runs (C8).

- ``promotion status``: the current promotion record (``null`` when there is none).
- ``promotion adopt <P-id>``: the promotion-recovery card takes over a ``submitted``
  promotion (writes the ledger under the canonical lock).
- ``promotion stage-champion <P-id>``: copy the candidate STRATEGY at ``candidate_sha`` to
  the champion path in this card's worktree; the holder task only (adopt first).

Prints one JSON envelope like ``card``: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"message": ...}}``. Exit codes: 0 done, 1 refused (PromotionError),
2 bad arguments or no runtime context.
"""

import argparse
import json

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.runtime.promotion import PromotionError, adopt, load, stage_champion_for

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


register_group("promotion", _setup)
