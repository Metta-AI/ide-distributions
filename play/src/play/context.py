"""The profile home and environment the operations run with."""

import os
from dataclasses import dataclass
from pathlib import Path

PLAY_ENV = (
    "SOFTMAX_PLAYER_CREDENTIAL",
    "COGAMES_API_URL",
    "RESEARCH_PLAYER_ID",
    "PLAY_WORKSPACE",
)


@dataclass
class Context:
    home: Path
    env: dict[str, str]


def profile_context() -> Context:
    """Use Hermes's routed home and secret scope, never a different profile's credential."""
    from agent.secret_scope import build_profile_secret_scope, current_secret_scope
    from hermes_constants import get_hermes_home

    home = Path(get_hermes_home())
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in (*PLAY_ENV, "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME")
        and not k.startswith("OPENROUTER_")
    }
    scope = current_secret_scope()
    env.update(build_profile_secret_scope(home) if scope is None else scope)
    env["HERMES_HOME"] = str(home)
    return Context(home, env)
