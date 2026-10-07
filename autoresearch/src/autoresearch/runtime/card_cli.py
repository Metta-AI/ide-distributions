"""``autoresearch card finish --outcome <o>``: end a card run through the trusted path.

Prints one JSON envelope like ``records``: ``{"ok": true, "data": <handoff record>}`` or
``{"ok": false, "error": {"message": ..., "problems": [...]}}``. Exit codes: 0 recorded,
1 validation problems (nothing recorded; fix them and rerun), 2 bad arguments or no card
worker context.
"""

import argparse
import json

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.runtime.card_finish import OUTCOMES, FinishUsageError, finish

EXIT_OK, EXIT_FAILURE, EXIT_BAD_ARGS = 0, 1, 2


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _fail(message: str, code: int, problems: list[str] | None = None) -> int:
    error: dict = {"message": message}
    if problems is not None:
        error["problems"] = problems
    _emit({"ok": False, "error": error})
    return code


def _finish(args: argparse.Namespace) -> int:
    try:
        result = finish(runctx.from_env(), args.outcome)
    except KeyError as error:
        return _fail(f"{error.args[0]} is not set", EXIT_BAD_ARGS)
    except FinishUsageError as error:
        return _fail(str(error), EXIT_BAD_ARGS)
    if not result.ok:
        return _fail("handoff validation failed; nothing recorded", EXIT_FAILURE, result.problems)
    _emit({"ok": True, "data": result.record})
    return EXIT_OK


def _setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    finish_parser = commands.add_parser("finish", help="commit, validate and hand off this run")
    finish_parser.add_argument("--outcome", required=True, choices=OUTCOMES)
    finish_parser.set_defaults(func=_finish)


register_group("card", _setup)
