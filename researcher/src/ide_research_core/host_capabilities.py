"""``host_capabilities``: what this profile's host has and where it can write.

It only looks: executables on ``PATH``, file permissions and mount options. It runs no command
and returns only executable/location paths and approval modes, never credentials. Both research profiles
register it (the orchestrator plugin and ``researcher_tools``).

A hosted workspace installs the profile read-only and makes only its own disk writable, so
``writable`` answers "can I build here?" for each place an agent is likely to try.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

EXECUTABLES = (
    "git", "python3", "uv", "gcc", "make", "nim", "node", "curl", "docker", "softmax", "coworld",
)
DOCKER_SOCKET = Path("/var/run/docker.sock")
HOSTED_PROFILES = Path("/opt/data/profiles")

DESCRIPTION = (
    "Check this profile's host without running anything: which executables are installed "
    "(git, python3, uv, gcc, make, nim, node, curl, docker, softmax, coworld), whether Docker "
    "has a daemon, which places are writable (home, profile, workspace, TMPDIR, /tmp, the "
    "installed games/ and venv, the profile cache), whether /tmp forbids running programs, "
    "whether this is a hosted workspace, and which installed or shipped games are missing a "
    "host requirement. runtime contains exact executable and location paths; games include "
    "binding argv and cwd. execution reports the current session approval constraints; "
    "discovery does not grant permission. Returns no credentials."
)


def writable(path: Path) -> bool:
    """Whether ``path`` can be written, or created inside its nearest existing parent."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return os.access(path, os.W_OK)


def _mount_options(mount_point: str) -> list[str] | None:
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return None  # not Linux
    for line in reversed(lines):  # the last mount over a path wins
        fields = line.split()
        if len(fields) >= 4 and fields[1] == mount_point:
            return fields[3].split(",")
    return None


class RuntimePaths(BaseModel):
    executables: dict[str, str | None]
    research_cli: str | None
    locations: dict[str, str]


def _executable(name: str, home: Path, env: Mapping[str, str]) -> str | None:
    venv = home / "local/venv/bin" / name
    return shutil.which(str(venv)) or shutil.which(name, path=env.get("PATH", ""))


def _games(home: Path, env: Mapping[str, str]) -> dict:
    from ide_research_core.games.loader import GameError, load_game
    from ide_research_core.games.readiness import binding_commands
    from ide_research_core.games.scope import list_games

    games = {}
    for entry in list_games({**env, "HERMES_HOME": str(home)}):
        try:
            game = load_game(Path(entry["root"]), entry["name"])
        except GameError as error:
            games[entry["name"]] = {"error": str(error)}
            continue
        games[entry["name"]] = {
            "root": entry["root"],
            "directory": entry["path"],
            "commands": binding_commands(game, Path(entry["root"]), _executable("python3", home, env)),
            "missing_tools": [name for name in game.host_tools if not _executable(name, home, env)],
            "missing_env": [name for name in game.host_env if not env.get(name)],
        }
    return games


def host_capabilities(home: Path, env: Mapping[str, str]) -> dict:
    executables = {name: _executable(name, home, env) for name in EXECUTABLES}
    cli = env.get("RESEARCH_CLI")
    places = {
        "home": Path(env.get("HOME") or Path.home()),
        "profile": home,
        "workspace": Path(env.get("RESEARCH_WORKSPACE") or home / "workspace"),
        "tmpdir": Path(env.get("TMPDIR") or "/tmp"),
        "tmp": Path("/tmp"),
        "installed_games": home / "games",
        "profile_venv": home / "local/venv",
        "game_cache": home / "cache",
    }
    tmp_options = _mount_options("/tmp")
    return {
        "hosted": HOSTED_PROFILES.is_dir(),
        "executables": {name: path is not None for name, path in executables.items()},
        "runtime": RuntimePaths(
            executables=executables,
            research_cli=shutil.which(cli, path=env.get("PATH", "")) if cli else None,
            locations={name: str(path.absolute()) for name, path in places.items()},
        ).model_dump(),
        "docker_daemon": DOCKER_SOCKET.exists(),
        "writable": {name: writable(path) for name, path in places.items()},
        "tmp_allows_programs": None if tmp_options is None else "noexec" not in tmp_options,
        "research_cli_configured": bool(cli),
        "research_cli_available": bool(cli and shutil.which(cli, path=env.get("PATH", ""))),
        "workspace_present": places["workspace"].is_dir(),
        "games": _games(home, env),
        "scope": "this profile's host, not the user's Mac or a worker container",
        "authorization": "Executable presence does not establish GitHub access or permission to run a command.",
    }


def _session() -> tuple[Path, dict[str, str]]:
    """This profile's home and environment inside the Hermes process (its own secret scope)."""
    from agent.secret_scope import build_profile_secret_scope, current_secret_scope
    from hermes_constants import get_hermes_home

    home = Path(get_hermes_home())
    scope = current_secret_scope()
    env = {**os.environ, **(build_profile_secret_scope(home) if scope is None else scope)}
    return home, env


def handle(args: dict | None = None, **_kwargs) -> str:
    try:
        from tools.approval import _unattended_contexts
        from tools.approval_context import _get_approval_mode, _get_session_platform

        data = host_capabilities(*_session())
        data["execution"] = {
            "platform": _get_session_platform(),
            "approval_mode": _get_approval_mode(),
            "unattended": [{"context": ctx.name, "mode": ctx.mode()} for ctx in _unattended_contexts()],
            "terminal": "Commands remain subject to native command guards and approval transport.",
            "execute_code": (
                "Local Python requires whole-script approval. An active unattended deny mode "
                "blocks it unless guards are disabled by runtime configuration. "
                "Use the normal tool approval flow; never reroute a denied action."
            ),
        }
        return json.dumps({"ok": True, "data": data})
    except Exception as error:  # noqa: BLE001 - tool boundary reports the failure
        return json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"})


def register(ctx, toolset: str) -> None:
    """Register ``host_capabilities`` in ``toolset`` (each profile's own plugin toolset)."""
    ctx.register_tool(
        name="host_capabilities",
        toolset=toolset,
        schema={
            "name": "host_capabilities",
            "description": DESCRIPTION,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=handle,
        description=DESCRIPTION,
    )
