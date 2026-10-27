"""Read-only host discovery without launching a shell or exposing environment values."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class WorkerRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    game: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    query: str = Field(default="", max_length=200)


class Capabilities:
    def __init__(self, context):
        self.context = context

    def worker_capabilities(self, assignee, game=None, query=""):
        """Inspect the installed worker, without creating a task or invoking a game binding."""
        from agent.secret_scope import build_profile_secret_scope
        from hermes_cli.profiles import get_profile_dir

        request = WorkerRead(assignee=assignee, game=game, query=query)
        profile = get_profile_dir(request.assignee)
        if not (profile / "config.yaml").is_file():
            return {"status": "missing_profile", "assignee": request.assignee}

        # Do not inherit the coordinator's modules, platform, task or credentials.
        env = {
            "PATH": os.environ["PATH"],
            **build_profile_secret_scope(profile),
            "HERMES_HOME": str(profile),
            "HERMES_PROFILE": request.assignee,
            "HERMES_SESSION_PLATFORM": "cli",
            "PYTHONPATH": os.pathsep.join(
                [
                    str(profile / "src"),
                    *(
                        path
                        for path in sys.path
                        if path
                        and not Path(path).resolve().is_relative_to(self.context.home.resolve())
                    ),
                ]
            ),
        }
        catalog = subprocess.run(
            [
                sys.executable,
                "-c",
                """import json, sys
from hermes_cli.plugins import discover_plugins
discover_plugins()
from ide_research_core.session_catalog import capabilities
print(json.dumps(capabilities(query=sys.argv[1])))
""",
                request.query,
            ],
            env=env,
            cwd=profile,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        result = {
            "status": "available",
            "assignee": request.assignee,
            "catalog": json.loads(catalog.stdout),
            "scope": "Installed worker CLI configuration; task overrides and approval gates still apply.",
            "execution_verified": False,
        }
        if request.game is not None:
            python = profile / "local/venv/bin/python"
            if not python.is_file():
                result["game"] = {"name": request.game, "status": "missing_runtime"}
                return result
            readiness = subprocess.run(
                [
                    str(python),
                    "-c",
                    """import json, os, sys
from ide_research_core.games.scope import list_games
from ide_research_core.games.loader import load_game
from ide_research_core.games.readiness import binding_readiness
from pathlib import Path
entry = next((entry for entry in list_games() if entry['name'] == sys.argv[1]), None)
if entry is None:
    print(json.dumps({'name': sys.argv[1], 'status': 'missing_game'}))
else:
    root = Path(entry['root'])
    game = load_game(root, entry['name'])
    print(json.dumps({'name': game.name, 'status': 'installed', 'root': str(root),
                      'bindings': game.bindings,
                      'readiness': binding_readiness(game, root, os.environ)}))
""",
                    request.game,
                ],
                env={**env, "PYTHONPATH": str(profile / "src")},
                cwd=profile,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            result["game"] = json.loads(readiness.stdout)
        return result

    def host_capabilities(self):
        env = self.context.env
        tools = {}
        for name in ("git", "python3", "softmax", "coworld"):
            installed = self.context.home / "local/venv/bin" / name
            tools[name] = bool(
                shutil.which(str(installed)) or shutil.which(name, path=env.get("PATH", ""))
            )
        cli = env.get("RESEARCH_CLI")
        return {
            "executables": tools,
            "research_cli_configured": bool(cli),
            "research_cli_available": bool(cli and shutil.which(cli, path=env.get("PATH", ""))),
            "scope": "this profile's host, not the user's Mac or a worker container",
            "workspace_present": Path(self.context.workspace).is_dir(),
            "authorization": "Executable presence does not establish GitHub access or permission to run a command.",
        }
