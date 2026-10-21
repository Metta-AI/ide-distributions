"""``research promotion``: submit, bootstrap, status and accept (see ``promotion``).

The orchestrator runs these in its own clone; researchers' deny list blocks them. Every
command prints one JSON object: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"reason", "message", "details"?}}``.
"""

import argparse
import json
import os
from pathlib import Path

from ide_research_core import promotion, workspace
from ide_research_core.cli import register_group
from ide_research_core.games.loader import GameError, load_game
from ide_research_core.io import gitops
from ide_research_core.records.store import RecordNotFound
from ide_research_core.softmax_tool.protocol import Refused, from_env

PLAYER_ENV = "RESEARCH_PLAYER_ID"
EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS = 0, 1, 2


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
            clone = Path(args.root) if args.root else workspace.workspace_from_env()
            game = os.environ.get(workspace.GAME_ENV) or workspace.DEFAULT_GAME
            league_id = load_game(workspace.DISTRIBUTION_ROOT, game).league_id
            player_id = os.environ[PLAYER_ENV]
            sm = from_env(os.environ, clone)
            data = handler(args, clone, sm, league_id, player_id)
        except KeyError as error:
            return _fail("bad_arguments", f"{error.args[0]} is not set", EXIT_BAD_ARGS)
        except GameError as error:
            return _fail("bad_arguments", str(error), EXIT_BAD_ARGS)
        except Refused as error:
            return _fail(error.reason, str(error), EXIT_FAILED, error.details)
        except RecordNotFound as error:
            return _fail("records", str(error), EXIT_FAILED)
        except workspace.WorkspaceError as error:
            return _fail(error.reason, str(error), EXIT_FAILED, error.details)
        except (gitops.GitError, RuntimeError, OSError, ValueError) as error:
            return _fail("platform", f"{type(error).__name__}: {error}", EXIT_FAILED)
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="this profile's clone (default: $RESEARCH_WORKSPACE)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    sub = command(
        "submit",
        lambda a, c, sm, league, player: promotion.submit(c, sm, a.experiment, league, player),
        "submit a promoted experiment's candidate to the league",
    )
    sub.add_argument("experiment")
    sub = command(
        "submit-requested",
        lambda a, c, sm, league, player: promotion.submit_requested(
            c, sm, a.version, a.strategy, a.request, league, player
        ),
        "submit a built version explicitly requested by the user, without an improvement claim",
    )
    sub.add_argument("--version", required=True)
    sub.add_argument("--strategy", required=True, help="canonical strategy matching the uploaded build")
    sub.add_argument("--request", required=True, help="the user's explicit submission request")
    sub = command(
        "bootstrap",
        lambda a, c, sm, league, player: promotion.bootstrap(c, sm, a.version, league, player),
        "submit the uploaded starter-policy version as the first champion",
    )
    sub.add_argument("--version", required=True, help="the uploaded version record, V-...")
    command(
        "pending",
        lambda a, c, sm, league, player: next(
            (p.to_dict() for p in reversed(promotion.load_all(c)) if p.stage in promotion.IN_FLIGHT), None,
        ),
        "read an in-flight promotion without advancing or submitting it",
    )
    command(
        "status",
        lambda a, c, sm, league, player: promotion.status(c, sm, league, player),
        "advance the promotion in flight one step and report the champion and any conflict",
    )
    command(
        "accept",
        lambda a, c, sm, league, player: promotion.accept(c, sm, league, player),
        "record the league's current champion as the loop's (a human decided)",
    )


register_group("promotion", setup)
