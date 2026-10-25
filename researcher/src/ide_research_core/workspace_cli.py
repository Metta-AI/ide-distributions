"""``research workspace``: integrate, merged, publish and bootstrap (see ``workspace``).

Every command prints one JSON object: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"reason", "message", "details"?}}``.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from ide_research_core import workspace
from ide_research_core.cli import register_group
from ide_research_core.games import scope
from ide_research_core.games.loader import GameError
from ide_research_core.io import gitops
from ide_research_core.io.dotenv import set_env_lines
from ide_research_core.records.store import RecordProblems

EXIT_OK, EXIT_FAILED, EXIT_BAD_ARGS = 0, 1, 2
WORKTREE_ENV = (
    "HERMES_KANBAN_WORKSPACE"  # survives into a Kanban worker's terminal commands
)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            if handler in (
                _integrate,
                _publish,
                _presentation,
                _records,
                _goals,
                _project_source,
                _policy_files,
            ):
                home = Path(os.environ["HERMES_HOME"])
                if (home / "plugins/researcher_guard").is_dir():
                    raise workspace.WorkspaceError(
                        "coordinator_required",
                        "Only the workspace coordinator may publish canonical changes",
                    )
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
        except (ValidationError, RecordProblems) as error:
            _emit(
                {
                    "ok": False,
                    "error": {"reason": "invalid_request", "message": str(error)},
                }
            )
            return EXIT_FAILED
        except KeyError as error:
            message = f"{error.args[0]} is not set"
            _emit(
                {"ok": False, "error": {"reason": "bad_arguments", "message": message}}
            )
            return EXIT_BAD_ARGS
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


def _clone(args) -> Path:
    return Path(args.root) if args.root else workspace.workspace_from_env()


def _integrate(args) -> dict:
    return workspace.integrate(
        _clone(args), branch=args.branch, commit=args.commit
    ).to_dict()


def _stage(args) -> dict:
    worktree = Path(args.worktree or os.environ.get(WORKTREE_ENV) or Path.cwd())
    return workspace.stage(worktree)


def _merged(args) -> dict:
    return workspace.merged(_clone(args), args.branch)


def _publish(args) -> dict:
    clone = _clone(args)
    return workspace.publish(
        clone,
        workspace.read_files(clone, args.path),
        args.message,
        expected_head=args.expected_head,
    )


def _presentation(args) -> dict:
    from ide_research_core.presentation import execute, presentation_request

    return execute(
        _clone(args),
        presentation_request.validate_json(sys.stdin.buffer.read(150001)),
    )


def _records(args) -> dict:
    from ide_research_core.workspace_records import RecordsRequest, execute

    return execute(
        _clone(args), RecordsRequest.model_validate_json(sys.stdin.buffer.read(150001))
    )


def _goals(args) -> dict:
    from ide_research_core.workspace_goals import GoalsRequest, execute

    return execute(
        _clone(args), GoalsRequest.model_validate_json(sys.stdin.buffer.read(150001))
    )


def _project_source(args) -> dict:
    from ide_research_core.source_schema import HermesProjectSource
    from ide_research_core.workspace_sources import execute

    payload = json.loads(sys.stdin.buffer.read(750001))
    request_id = payload.pop("request_id", "")
    return execute(
        _clone(args), HermesProjectSource.model_validate(payload), request_id
    )


def _policy_files(args) -> dict:
    from ide_research_core.source_schema import HermesPolicyFiles
    from ide_research_core.workspace_sources import execute

    payload = json.loads(sys.stdin.buffer.read(100001))
    request_id = payload.pop("request_id", "")
    return execute(_clone(args), HermesPolicyFiles.model_validate(payload), request_id)


def _bootstrap(args) -> dict:
    """``--game G`` prepares this profile's clone of another game than ``RESEARCH_GAME``
    (``games.scope``: ``canonical-G.git`` beside ``RESEARCH_CANONICAL``, ``workspace-G``)."""
    home = Path(os.environ["HERMES_HOME"])
    env = scope.env_for_game(os.environ, args.game)
    game = env.get(workspace.GAME_ENV)
    clone = Path(args.root) if args.root else workspace.workspace_from_env(env)
    bare = Path(env[workspace.CANONICAL_ENV])
    try:
        game_root = scope.locate_game(game, env) if game is not None else None
    except GameError as error:
        raise workspace.WorkspaceError("setup", str(error)) from None
    actions = workspace.bootstrap(clone, bare, game, game_root=game_root)
    # Installed games load their skills from here (researcher skills.external_dirs).
    scope.installed_games_dir(env).mkdir(parents=True, exist_ok=True)
    shim = workspace.write_cli_shim(home)
    if set_env_lines(home / ".env", {"RESEARCH_CLI": str(shim)}):
        actions.append(f"set RESEARCH_CLI={shim} in {home / '.env'}")
    return {
        "actions": actions,
        "game": game,
        "workspace": str(clone),
        "canonical": str(bare),
        "cli": str(shim),
    }


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root", help="this profile's clone (default: $RESEARCH_WORKSPACE)"
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    sub = command("stage", _stage, "preserve task output without integrating it")
    sub.add_argument("--worktree", help="assigned worktree (default: current one)")
    sub = command(
        "integrate",
        _integrate,
        "integrate a staged branch after coordinator acceptance",
    )
    sub.add_argument("--branch", required=True, help="staged native task branch")
    sub.add_argument("--commit", required=True, help="exact reviewed commit")
    sub = command("merged", _merged, "is a card branch's commit in canonical main")
    sub.add_argument("--branch", required=True)
    sub = command(
        "publish", _publish, "commit the given paths and push main (orchestrator)"
    )
    sub.add_argument("--message", required=True)
    sub.add_argument("--path", action="append", required=True)
    sub.add_argument(
        "--expected-head",
        help="reject publication unless canonical main still has this commit",
    )
    command(
        "presentation",
        _presentation,
        "read or conditionally save UI state from a JSON request on stdin",
    )
    command(
        "goals",
        _goals,
        "read or evaluate native research goal context from JSON on stdin",
    )
    command("project-source", _project_source, "read or edit unpublished project files")
    command(
        "policy-files",
        _policy_files,
        "read or edit source bound to native version provenance",
    )
    command(
        "records", _records, "read or update native research records from JSON on stdin"
    )
    sub = command(
        "bootstrap",
        _bootstrap,
        "create or clone this profile's workspace of a game; write the CLI shim",
    )
    sub.add_argument("--game", help="a shipped or installed game (default: $RESEARCH_GAME)")


register_group("workspace", setup)
