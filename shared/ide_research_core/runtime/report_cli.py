"""``autoresearch report baseline --since <YYYY-MM-DD> --json``: performance and cost since a
UTC date (``runtime.baseline``).

Prints ``{"ok": true, "data": {"cards", "days", "experiments", "builds", "key"}, "warnings":
[...]}``. Read only, so allowed in card contexts. Card workers' sessions (Hermes sources
``kanban`` and ``cli``) are counted under ``cli``; any source other than the four named ones is
an extra key in ``estimated_usd_by_source``. Exit codes: 0 ok, 2 bad arguments or no run
context. Always JSON; ``--json`` is accepted for clarity.
"""

import argparse
import datetime
import json
import os

from ide_research_core import runctx
from ide_research_core.cli import register_group
from ide_research_core.queue.select import queue_from_env
from ide_research_core.runtime import baseline
from ide_research_core.runtime.preflight import _urllib_get

EXIT_OK, EXIT_BAD_ARGS = 0, 2


def _error(message: str) -> int:
    print(json.dumps({"ok": False, "error": {"message": message}}))
    return EXIT_BAD_ARGS


def _baseline(args: argparse.Namespace) -> int:
    try:
        since = datetime.date.fromisoformat(args.since)
    except ValueError:
        return _error(f"--since must be a date like 2026-10-09, not {args.since!r}")
    try:
        ctx = runctx.from_env()
    except KeyError as error:
        return _error(f"{error.args[0]} is not set")
    queue = queue_from_env(os.environ, ctx)
    result = baseline.build(ctx, os.environ, since, _urllib_get, queue)
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return EXIT_OK


def _setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    sub = commands.add_parser(
        "baseline",
        help="performance and cost since a UTC date: cards, days by source (card workers "
        "under cli; other sources as extra keys), experiments, builds, the OpenRouter key",
    )
    sub.add_argument("--since", required=True, help="first UTC day to report, YYYY-MM-DD")
    sub.add_argument("--json", action="store_true", help="accepted; the output is always JSON")
    sub.set_defaults(func=_baseline)


register_group("report", _setup)
