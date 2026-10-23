"""Which game a command works on, and where that game's folder, repository and clone live.

A host researches several games. Each game has its own canonical repository,
``canonical-<game>.git`` beside ``RESEARCH_CANONICAL``, and each profile has its own clone of it.
Research commands read three variables, ``RESEARCH_GAME``, ``RESEARCH_CANONICAL`` and
``RESEARCH_WORKSPACE``; this module points them at one game:

- ``env_for_game(env, game)`` by naming rule, for a caller that chooses the game (the
  orchestrator's tools, card creation, ``workspace bootstrap --game``). Paths are always
  derived from the profile's own variables, in which ``RESEARCH_GAME`` is the host's default
  game: both functions keep those as ``RESEARCH_PROFILE_GAME``, ``_CANONICAL`` and ``_WORKSPACE``
  while they point elsewhere.
- ``env_for_worktree(env, worktree)`` from a card worktree: its clone, that clone's ``origin``
  and the game in the origin's name. The CLI entry point applies it inside a Kanban worker, so a
  card of any game runs its commands against that game.

The episode budget stays one host-wide allowance for every game: both functions keep its file
(``RESEARCH_EPISODE_BUDGET``, by default ``episode-budget.db`` in the default game's canonical
repository) where the profile's own environment puts it; ``budget_path`` reads it.

Game folders are found by ``locate_game``: the games shipped with the distribution first, then
the games installed on the host (``<root>/games``, written by ``research game install``). Core
code never names a game.
"""

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from ide_research_core.games.loader import Game, GameError, load_game

GAME_ENV = "RESEARCH_GAME"
CANONICAL_ENV = "RESEARCH_CANONICAL"
WORKSPACE_ENV = "RESEARCH_WORKSPACE"
HOME_ENV = "HERMES_HOME"
WORKER_ENV = "HERMES_KANBAN_WORKSPACE"
BUDGET_ENV = "RESEARCH_EPISODE_BUDGET"

# A game name is a directory name and part of repository and clone names.
GAME_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_CANONICAL_NAME = re.compile(r"canonical-([a-z0-9][a-z0-9-]{0,39})\.git")

DISTRIBUTION_ROOT = Path(__file__).resolve().parents[3]  # the profile: games/ ships here


def check_game_name(name: str) -> str:
    if not isinstance(name, str) or not GAME_NAME.fullmatch(name):
        raise GameError(
            f"game name {name!r} must be lowercase letters, digits and hyphens (at most 40)"
        )
    return name


def host_root(env: Mapping[str, str] = os.environ) -> Path:
    """The Hermes root that holds ``profiles/``: shared by every profile on the host."""
    home = Path(env[HOME_ENV])
    return home.parent.parent if home.parent.name == "profiles" else home


def installed_games_dir(env: Mapping[str, str] = os.environ) -> Path:
    return host_root(env) / "games"


def game_roots(env: Mapping[str, str] = os.environ) -> list[Path]:
    """Directories that hold a ``games/`` folder, in lookup order: shipped, then installed."""
    roots = [DISTRIBUTION_ROOT]
    if HOME_ENV in env:
        roots.append(host_root(env))
    return roots


def locate_game(name: str, env: Mapping[str, str] = os.environ) -> Path:
    """The root whose ``games/<name>/GAME.md`` exists (shipped games win)."""
    check_game_name(name)
    for root in game_roots(env):
        if (root / "games" / name / "GAME.md").is_file():
            return root
    raise GameError(f"game {name!r}: games/{name}/GAME.md not found (not shipped or installed)")


def load_named_game(name: str, env: Mapping[str, str] = os.environ) -> Game:
    return load_game(locate_game(name, env), name)


def list_games(env: Mapping[str, str] = os.environ) -> list[dict]:
    """Every shipped or installed game folder, with where it is and whether it has a research
    workspace (a canonical repository) on this host. A shipped name hides an installed one."""
    found: dict[str, dict] = {}
    for kind, root in zip(("shipped", "installed"), game_roots(env), strict=False):
        for path in sorted((root / "games").glob("*/GAME.md")):
            name = path.parent.name
            if name in found or not GAME_NAME.fullmatch(name):
                continue  # ``_template`` and other non-game folders
            found[name] = {"name": name, "kind": kind, "root": str(root), "path": str(path.parent)}
    for entry in found.values():
        entry["workspace"] = (
            CANONICAL_ENV in env and (canonical_for(env, entry["name"]) / "HEAD").exists()
        )
    return list(found.values())


def canonical_for(env: Mapping[str, str], game: str) -> Path:
    canonical = Path(env[CANONICAL_ENV])
    if game == env.get(GAME_ENV):
        return canonical
    return canonical.parent / f"canonical-{check_game_name(game)}.git"


def clone_for(env: Mapping[str, str], game: str) -> Path:
    if game == env.get(GAME_ENV):
        return Path(env.get(WORKSPACE_ENV) or Path(env[HOME_ENV]) / "workspace")
    return Path(env[HOME_ENV]) / f"workspace-{check_game_name(game)}"


def budget_path(env: Mapping[str, str]) -> Path:
    """The host-wide episode budget's file, the same for every game (module docstring)."""
    return Path(env.get(BUDGET_ENV) or Path(env[CANONICAL_ENV]) / "episode-budget.db")


def _keep_budget(env: Mapping[str, str], scoped: dict[str, str]) -> None:
    if CANONICAL_ENV in env:
        scoped[BUDGET_ENV] = str(budget_path(env))


# The profile's own value of each scoped variable, kept while it points elsewhere.
_PROFILE = {
    name: name.replace("RESEARCH_", "RESEARCH_PROFILE_")
    for name in (GAME_ENV, CANONICAL_ENV, WORKSPACE_ENV)
}


def _profile_env(env: Mapping[str, str]) -> dict[str, str]:
    """``env`` with the profile's own research variables, undoing any earlier scoping."""
    base = {key: value for key, value in env.items() if key not in _PROFILE.values()}
    for name, kept in _PROFILE.items():
        if kept in env:
            base[name] = env[kept]
    return base


def _scope(profile: Mapping[str, str], game: str, canonical: str, clone: str) -> dict[str, str]:
    if GAME_ENV in profile and HOME_ENV in profile:
        # Keep the default game's clone even when RESEARCH_WORKSPACE was left to its default.
        profile = {**profile, WORKSPACE_ENV: str(clone_for(profile, profile[GAME_ENV]))}
    scoped = dict(profile)
    _keep_budget(profile, scoped)
    for name, kept in _PROFILE.items():
        if name in profile:
            scoped[kept] = profile[name]
    scoped.update({GAME_ENV: game, CANONICAL_ENV: canonical, WORKSPACE_ENV: clone})
    return scoped


def env_for_game(env: Mapping[str, str], game: str | None) -> dict[str, str]:
    """``env`` with the three research variables pointed at ``game`` (None: unchanged)."""
    if game is None:
        return dict(env)
    profile = _profile_env(env)
    if game == profile.get(GAME_ENV):
        return profile
    return _scope(
        profile, game, str(canonical_for(profile, game)), str(clone_for(profile, game))
    )


def _git(path: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False, timeout=30
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _game_of_origin(origin: str | None) -> str | None:
    match = _CANONICAL_NAME.fullmatch(Path(origin).name) if origin else None
    return match.group(1) if match else None


def game_of_clone(clone: Path) -> str | None:
    """The game whose canonical repository ``clone`` pushes to, from its ``origin`` name."""
    return _game_of_origin(_git(clone, "remote", "get-url", "origin"))


def env_for_worktree(env: Mapping[str, str], worktree: Path) -> dict[str, str]:
    """``env`` pointed at the game of the card ``worktree``: its clone and that clone's
    ``origin``. Unchanged for a card of the profile's own game, or outside a research clone."""
    scoped = dict(env)
    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None:
        return scoped
    clone = Path(common).parent
    origin = _git(clone, "remote", "get-url", "origin")
    game = _game_of_origin(origin)
    if game is None or game == env.get(GAME_ENV):
        return scoped
    return _scope(_profile_env(env), game, origin, str(clone))


def worker_env(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Inside a Kanban worker, ``env`` pointed at its card's game; otherwise unchanged."""
    worktree = env.get(WORKER_ENV)
    return env_for_worktree(env, Path(worktree)) if worktree else dict(env)
