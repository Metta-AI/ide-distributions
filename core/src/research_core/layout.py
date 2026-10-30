"""The workspace directory model (design Figure 2 and B.11).

One workspace per sandbox, shared by every profile. It holds the workspace environment under
``tools/``, the workspace store under ``.state/`` and one folder per Coworld, named from the
platform Coworld id. Stdlib only: the Hermes plugin imports this module.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_COWORLD_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def folder_name_for(coworld_id: str) -> str:
    """The folder of a Coworld: its platform id with hyphens replaced by underscores.

    Hyphens break ``python -m`` style imports of game tools (B.11), so the folder never has
    one. The id itself is the only stored identifier; display names are resolved live.
    """
    if not _COWORLD_ID.match(coworld_id):
        raise ValueError(f"not a Coworld id: {coworld_id!r} (letters, digits, '_' and '-' only)")
    return coworld_id.replace("-", "_")


@dataclass(frozen=True)
class Coworld:
    coworld_id: str
    root: Path

    @property
    def folder_name(self) -> str:
        return self.root.name

    @property
    def code(self) -> Path:
        """The policy repository: ``main`` holds the policy and ``mixin/``."""
        return self.root / "code"

    @property
    def mixin(self) -> Path:
        return self.code / "mixin"

    @property
    def source(self) -> Path:
        """The clone of the Coworld's public repository at the release-pinned commit."""
        return self.root / "source"

    @property
    def worktrees(self) -> Path:
        return self.root / "worktrees"

    @property
    def documents(self) -> Path:
        """The agents' own notes and files; never in any prompt."""
        return self.root / "documents"

    @property
    def state(self) -> Path:
        return self.root / ".state"

    @property
    def store_path(self) -> Path:
        return self.state / "store.db"

    def worktree_for_task(self, task_id: int) -> Path:
        return self.worktrees / f"task-{task_id}"

    def worktree_for_change(self, n: int) -> Path:
        return self.worktrees / f"orch-{n}"

    def ensure_dirs(self) -> None:
        for path in (self.root, self.worktrees, self.documents, self.state):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Workspace:
    root: Path
    venv: Path

    @property
    def tools(self) -> Path:
        """The workspace environment and mixin-declared toolchains."""
        return self.root / "tools"

    @property
    def venv_python(self) -> Path:
        return self.venv / "bin" / "python"

    @property
    def state(self) -> Path:
        return self.root / ".state"

    @property
    def store_path(self) -> Path:
        """The workspace store: the Coworld registry, workspace directives and settings."""
        return self.state / "store.db"

    def coworld(self, coworld_id: str) -> Coworld:
        return Coworld(coworld_id=coworld_id, root=self.root / folder_name_for(coworld_id))

    def ensure_dirs(self) -> None:
        for path in (self.root, self.tools, self.state):
            path.mkdir(parents=True, exist_ok=True)


def workspace_root(env: Mapping[str, str] = os.environ) -> Path:
    """``IDE_WORKSPACE``, else ``$HERMES_REAL_HOME/workspace``, else ``~/.hermes/workspace``."""
    if env.get("IDE_WORKSPACE"):
        return Path(env["IDE_WORKSPACE"]).expanduser()
    if env.get("HERMES_REAL_HOME"):
        return Path(env["HERMES_REAL_HOME"]).expanduser() / "workspace"
    return Path.home() / ".hermes" / "workspace"


def from_env(env: Mapping[str, str] = os.environ) -> Workspace:
    root = workspace_root(env)
    venv = (
        Path(env["IDE_WORKSPACE_ENV"]).expanduser()
        if env.get("IDE_WORKSPACE_ENV")
        else root / "tools" / "venv"
    )
    return Workspace(root=root, venv=venv)
