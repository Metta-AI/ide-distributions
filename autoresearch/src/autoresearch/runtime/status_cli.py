"""``autoresearch status --json``: a read-only snapshot of the agent (``runtime.status``).

Prints ``{"ok": true, "data": {...}, "warnings": [...]}``. Allowed in card contexts (it only
reads). Exit codes: 0 ok, 2 bad arguments or no run context. Always JSON; ``--json`` is
accepted for clarity.
"""

import argparse
import datetime
import json
import os

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.games.loader import load_game
from autoresearch.queue.select import queue_from_env
from autoresearch.runtime import status
from autoresearch.runtime.bootstrap import REPO_ROOT
from autoresearch.runtime.preflight import _urllib_get
from autoresearch.runtime.promotion import league_check
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env

EXIT_OK, EXIT_BAD_ARGS = 0, 2


def _league_check_fn(ctx: runctx.RunContext) -> status.LeagueCheckFn:
    def check(now: datetime.datetime, grace: datetime.timedelta):
        game = load_game(REPO_ROOT, os.environ.get("AUTORESEARCH_GAME") or "gota")
        sm = softmax_from_env(os.environ, ctx.canonical)
        return league_check(ctx, sm, game.league_id, now, grace)

    return check


def _status(args: argparse.Namespace) -> int:
    try:
        ctx = runctx.from_env()
    except KeyError as error:
        print(json.dumps({"ok": False, "error": {"message": f"{error.args[0]} is not set"}}))
        return EXIT_BAD_ARGS
    queue = queue_from_env(os.environ, ctx)
    result = status.build(ctx, os.environ, _urllib_get, queue, _league_check_fn(ctx))
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return EXIT_OK


def _setup(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="accepted; the output is always JSON")
    parser.set_defaults(func=_status)


register_group("status", _setup)
