"""``autoresearch game show [--name G]``: print the loaded game contract as JSON.

Prints ``{"ok": true, "data": {name, bindings, reference_types, outcome_metrics,
performance_guardrails, min_episodes_floor, league_id, noise_source, layers, compiler,
path}}``: the validated ``GAME.md`` front matter (``games.loader.load_game``) and the file's
path. The guide body is not printed; read ``path`` for it. ``--name`` defaults to
``AUTORESEARCH_GAME``. Exit 1 when the contract is missing or invalid, 2 when no game is named.
"""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from autoresearch.cli import register_group
from autoresearch.games.loader import GameError, load_game

GAME_ENV = "AUTORESEARCH_GAME"
_REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ lives at the project root


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _show(args: argparse.Namespace) -> int:
    name = args.name or os.environ.get(GAME_ENV)
    if not name:
        _emit({"ok": False, "error": {"message": f"pass --name or set {GAME_ENV}"}})
        return 2
    try:
        game = load_game(_REPO_ROOT, name)
    except GameError as error:
        _emit({"ok": False, "error": {"message": str(error)}})
        return 1
    data = asdict(game)
    del data["guide"]
    data["path"] = str(_REPO_ROOT / "games" / name / "GAME.md")
    _emit({"ok": True, "data": data})
    return 0


def setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    sub = commands.add_parser("show", help="print the game contract (GAME.md front matter)")
    sub.add_argument("--name", help=f"game directory under games/ (default ${GAME_ENV})")
    sub.set_defaults(func=_show)


register_group("game", setup)
