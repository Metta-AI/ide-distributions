"""Profile configuration migrations (Overview C9). Bootstrap applies them on every run.

Hermes keeps the installed ``config.yaml`` on a profile update, so a setting that must reach
existing installs is applied here with ``hermes config set`` (verified to write nested keys,
``docs/plans/findings/hermes-facts.md`` item 8). Policies:

- ``ALWAYS``: set whenever the profile value differs.
- ``MISSING_ONLY``: set only when the key is absent (the installer may tune it).
- ``UNSET``: removed with ``hermes config unset`` when present.

``apply`` reads the profile ``config.yaml`` first and runs Hermes only for keys that need a
change, so a run with nothing to migrate starts no process. The shipped ``config.yaml`` must
agree with these tables (a unit test checks it).

Not covered yet (Plan 03 Task 5.1 hardens this module): "auto-update off" and "the curator
excludes shipped skills" have no verified Hermes key in v0.21.5, so they are not set here.
``kanban.*`` rows govern only ``hermes -p <profile> kanban dispatch`` and profile tools; the
gateway dispatcher reads the host root config, which preflight checks (A3).
"""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

# argv -> None; raises on failure. ``home`` is the profile home the command must act on.
Runner = Callable[[list[str], Path], None]

ENV_PASSTHROUGH = [
    "SOFTMAX_PLAYER_CREDENTIAL",
    "AUTORESEARCH_PLAYER_ID",
    "AUTORESEARCH_WORKSPACE",
    "AUTORESEARCH_WORKSPACE_REMOTE",
    "AUTORESEARCH_GAME",
    "AUTORESEARCH_SOFTMAX",
    "AUTORESEARCH_CLI",
]
APPROVAL_DENY = [
    "coworld player use*",
    "coworld submit*",
    "coworld upload-policy*",
    "coworld xp-request create*",
    "softmax login*",
    "git push -f*",
    "git push*--force*",
    "git push* -f*",
]
SKILLS_STAGING_RELATIVE = Path("local") / "skills-staging"


def always(home: Path) -> dict[str, object]:
    """C9 "always" rows for the profile at ``home``."""
    return {
        "plugins.enabled": ["autoresearch"],
        "kanban.auto_decompose": False,
        "kanban.review_dispatch": False,
        "kanban.auto_subscribe_on_create": False,
        "memory.memory_enabled": True,
        "memory.user_profile_enabled": False,
        "memory.write_approval": False,
        "auxiliary.background_review.enabled": False,
        "approvals.mode": "off",
        "approvals.deny": APPROVAL_DENY,
        "terminal.env_passthrough": ENV_PASSTHROUGH,
        "skills.create_dir": str(Path(home) / SKILLS_STAGING_RELATIVE),
    }


MISSING_ONLY: dict[str, object] = {
    "kanban.max_in_progress": 2,
    "tool_loop_guardrails.hard_stop_enabled": True,
    "agent.max_turns": 150,
    "agent.run_budget_seconds": 2700,
}
UNSET = ("kanban.default_assignee",)

_ABSENT = object()


def _lookup(config: dict, dotted: str):
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def _cli_value(value: object) -> str:
    """The value as ``hermes config set`` parses it (YAML; flow-style lists)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value)  # JSON arrays are YAML flow sequences
    return str(value)


def hermes_runner(argv: list[str], home: Path) -> None:
    """Run ``hermes <argv>`` against the profile at ``home``."""
    env = {**os.environ, "HERMES_HOME": str(home)}
    subprocess.run(["hermes", *argv], env=env, check=True, capture_output=True, text=True)


def read_config(home: Path) -> dict:
    path = Path(home) / "config.yaml"
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def pending(home: Path) -> list[list[str]]:
    """The ``hermes`` argv lists that would bring the profile config in line with C9."""
    config = read_config(home)
    commands = []
    for key, value in always(home).items():
        if _lookup(config, key) != value:
            commands.append(["config", "set", key, _cli_value(value)])
    for key, value in MISSING_ONLY.items():
        if _lookup(config, key) is _ABSENT:
            commands.append(["config", "set", key, _cli_value(value)])
    for key in UNSET:
        if _lookup(config, key) not in (_ABSENT, None, ""):
            commands.append(["config", "unset", key])
    return commands


def apply(runner: Runner, home: Path) -> list[list[str]]:
    """Apply the C9 migrations to the profile at ``home``; return the commands run."""
    commands = pending(home)
    for argv in commands:
        runner(argv, Path(home))
    return commands
