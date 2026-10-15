"""Where a research command runs: the profile home, its clone of the canonical workspace, and
the card worktree when the command runs inside one.

A Kanban worker's terminal commands see ``HERMES_KANBAN_WORKSPACE`` (the card worktree);
Hermes removes the task and run ids from their environment, so a command that needs the card
derives it from the worktree's branch (``card/<id>``).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_HERMES_HOME = "HERMES_HOME"
ENV_WORKSPACE = "RESEARCH_WORKSPACE"
ENV_WORKER = "HERMES_KANBAN_WORKSPACE"


@dataclass(frozen=True)
class RunContext:
    hermes_home: Path
    canonical: Path  # this profile's clone of the canonical workspace
    worker: Path | None  # the card worktree, inside a Kanban worker

    @property
    def profile(self) -> str:
        """The install name of this profile: the profile home's name."""
        return self.hermes_home.name


def from_env(env: Mapping[str, str] = os.environ) -> RunContext:
    hermes_home = Path(env[ENV_HERMES_HOME])
    canonical = Path(env.get(ENV_WORKSPACE) or hermes_home / "workspace")
    worker = env.get(ENV_WORKER)
    return RunContext(hermes_home, canonical, Path(worker) if worker else None)


def runtime_dir(ctx: RunContext) -> Path:
    """Disposable, gitignored state of this clone (locks, caches)."""
    return ctx.canonical / ".supervisor" / "runtime"
