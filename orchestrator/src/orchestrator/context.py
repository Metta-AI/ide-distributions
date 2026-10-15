"""Profile paths and environment; no Hermes dependency in the gate subprocess."""

import os
from dataclasses import dataclass
from pathlib import Path

RESEARCH_ENV = (
    "SOFTMAX_PLAYER_CREDENTIAL",
    "COGAMES_API_URL",
    "RESEARCH_PLAYER_ID",
    "RESEARCH_GAME",
    "RESEARCH_WORKSPACE",
    "RESEARCH_CANONICAL",
)


@dataclass
class Context:
    home: Path
    env: dict[str, str]

    @property
    def root(self) -> Path:
        return self.home.parent.parent if self.home.parent.name == "profiles" else self.home

    @property
    def workspace(self) -> Path:
        return Path(self.env.get("RESEARCH_WORKSPACE") or self.home / "workspace")

    @property
    def board_path(self) -> Path:
        return self.root / "kanban" / "boards" / "autoresearch" / "kanban.db"

    @property
    def storage_path(self) -> Path:
        return self.home / "plugin-data" / "orchestrator" / "data.db"


def subprocess_context() -> Context:
    return Context(Path(os.environ["HERMES_HOME"]), dict(os.environ))


def profile_context() -> Context:
    """Use Hermes's routed home and secret scope, never a different profile's credential."""
    from agent.secret_scope import build_profile_secret_scope, current_secret_scope
    from hermes_constants import get_hermes_home

    home = Path(get_hermes_home())
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (*RESEARCH_ENV, "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME")
        and not k.startswith("OPENROUTER_")
    }
    scope = current_secret_scope()
    env.update(build_profile_secret_scope(home) if scope is None else scope)
    env["HERMES_HOME"] = str(home)
    return Context(home, env)
