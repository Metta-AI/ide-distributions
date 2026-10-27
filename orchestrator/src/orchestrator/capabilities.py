"""Read-only host discovery without launching a shell or exposing environment values."""

import shutil
from pathlib import Path


class Capabilities:
    def __init__(self, context):
        self.context = context

    def host_capabilities(self):
        env = self.context.env
        tools = {}
        for name in ("git", "python3", "softmax", "coworld"):
            installed = self.context.home / "local/venv/bin" / name
            tools[name] = bool(shutil.which(str(installed)) or shutil.which(name, path=env.get("PATH", "")))
        cli = env.get("RESEARCH_CLI")
        return {
            "executables": tools,
            "research_cli_configured": bool(cli),
            "research_cli_available": bool(cli and shutil.which(cli, path=env.get("PATH", ""))),
            "scope": "this profile's host, not the user's Mac or a worker container",
            "workspace_present": Path(self.context.workspace).is_dir(),
            "authorization": "Executable presence does not establish GitHub access or permission to run a command.",
        }
