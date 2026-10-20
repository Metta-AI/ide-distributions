"""``research workspace``: integrate, merged, publish and bootstrap (see ``workspace``).

Every command prints one JSON object: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"reason", "message", "details"?}}``.
"""

import argparse
import json
import os
from pathlib import Path

from ide_research_core import workspace
from ide_research_core.cli import register_group
from ide_research_core.io import gitops
from ide_research_core.io.dotenv import set_env_lines

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS = 0, 1, 2
WORKTREE_ENV = "HERMES_KANBAN_WORKSPACE"  # survives into a Kanban worker's terminal commands


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            data = handler(args)
        except workspace.WorkspaceError as error:
            failure = {"reason": error.reason, "message": str(error)}
            if error.details:
                failure["details"] = error.details
            _emit({"ok": False, "error": failure})
            return EXIT_FAILED
        except gitops.GitError as error:
            _emit({"ok": False, "error": {"reason": "git", "message": str(error)}})
            return EXIT_FAILED
        except KeyError as error:
            message = f"{error.args[0]} is not set"
            _emit({"ok": False, "error": {"reason": "bad_arguments", "message": message}})
            return EXIT_BAD_ARGS
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


def _clone(args) -> Path:
    return Path(args.root) if args.root else workspace.workspace_from_env()


def _integrate(args) -> dict:
    if args.branch:
        return workspace.integrate(_clone(args), branch=args.branch).to_dict()
    worktree = Path(args.worktree or os.environ.get(WORKTREE_ENV) or Path.cwd())
    return workspace.integrate(_clone(args), worktree=worktree).to_dict()


def _merged(args) -> dict:
    return workspace.merged(_clone(args), args.branch)


def _publish(args) -> dict:
    clone = _clone(args)
    return workspace.publish(clone, workspace.read_files(clone, args.path), args.message)


def _bootstrap(args) -> dict:
    home = Path(os.environ["HERMES_HOME"])
    clone = _clone(args)
    bare = Path(os.environ[workspace.CANONICAL_ENV])
    game = os.environ.get(workspace.GAME_ENV) or workspace.DEFAULT_GAME
    actions = workspace.bootstrap(clone, bare, game)
    shim = workspace.write_cli_shim(home)
    if set_env_lines(home / ".env", {"RESEARCH_CLI": str(shim)}):
        actions.append(f"set RESEARCH_CLI={shim} in {home / '.env'}")
    return {"actions": actions, "workspace": str(clone), "canonical": str(bare), "cli": str(shim)}


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="this profile's clone (default: $RESEARCH_WORKSPACE)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    sub = command("integrate", _integrate, "merge a card into canonical main (the card's last step)")
    target = sub.add_mutually_exclusive_group()
    target.add_argument("--worktree", help="the card worktree (default: the current one)")
    target.add_argument("--branch", help="a pushed card branch, e.g. card/<id> (orchestrator)")
    sub = command("merged", _merged, "is a card branch's commit in canonical main")
    sub.add_argument("--branch", required=True)
    sub = command("publish", _publish, "commit the given paths and push main (orchestrator)")
    sub.add_argument("--message", required=True)
    sub.add_argument("--path", action="append", required=True)
    command("bootstrap", _bootstrap, "create or clone this profile's workspace; write the CLI shim")


register_group("workspace", setup)
