"""Profile configuration migrations (Overview C9). Bootstrap applies them on every run.

Hermes keeps the installed ``config.yaml`` on a profile update, so a setting that must reach
existing installs is applied here with ``hermes config set`` (verified to write nested keys,
``docs/plans/findings/hermes-facts.md`` item 8). Policies:

- ``ALWAYS``: set whenever the profile value differs.
- ``MISSING_ONLY``: set only when the key is absent (the installer may tune it).
- ``UPGRADE_DEFAULTS``: key -> (old default, new default). A value equal to the old default
  (an earlier release shipped it, or Hermes' own default) is raised to the new one; any other
  value is the installer's and is kept.
- ``UNSET``: removed with ``hermes config unset`` when present.
- ``ENV_OVERRIDES``: set from an environment variable when it is set and differs (the
  installer's ``OPENROUTER_MODEL`` in the profile ``.env`` becomes ``model.default``: Hermes
  workers read the model only from ``config.yaml``). Unset leaves the value alone. The names
  are on ``terminal.env_passthrough``, so a cron run under the gateway receives them (A4).

``apply`` reads the profile ``config.yaml`` first and runs Hermes only for keys that need a
change, so a run with nothing to migrate starts no process. The shipped ``config.yaml`` must
agree with these tables (a unit test checks it).

Hermes v0.21.5 has no auto-update: it only checks for updates passively, and ``updates.check:
false`` turns that off ("auto-update off", C9). The curator only archives skills that have a
``created_by: agent`` record or, with ``curator.prune_builtins``, bundled built-ins
(``tools/skill_usage.py list_agent_created_skill_names``). Shipped skills have neither, and
agent-created skills go to ``skills.create_dir`` (outside ``skills/``), so the curator already
leaves shipped skills alone; ``curator.prune_builtins: false`` pins that. Hermes has no
per-skill curator exclusion key (``docs/plans/findings/hermes-facts.md``, Task 5.1 observations).
``kanban.*`` rows govern only ``hermes -p <profile> kanban dispatch`` and profile tools; the
gateway dispatcher reads the host root config, which preflight checks (A3).
"""

import json
import os
import subprocess
from collections.abc import Callable, Mapping
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
    "AUTORESEARCH_QUEUE",  # the serial fallback selects the queue through it (A4)
    "OPENROUTER_MODEL",  # bootstrap applies it to model.default (ENV_OVERRIDES)
    "AUTORESEARCH_INBOX_WRITERS",  # queue-keeper's routing adopts direct inbox writes by owner
    "AUTORESEARCH_CARD_MODELS",  # queue-keeper creates cards with the per-mode model
    # spend caps (runtime.spend), read by queue-keeper and health
    "AUTORESEARCH_MAX_CARD_RUNS",
    "AUTORESEARCH_MAX_CARD_USD",
    "AUTORESEARCH_DAILY_BUDGET_USD",
    "AUTORESEARCH_FALLBACK_PRICES",
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
    "hermes kanban create*",
    "hermes kanban link*",
    "hermes kanban --board * create*",
    "hermes kanban --board * link*",
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
        "updates.check": False,
        "curator.prune_builtins": False,
    }


MISSING_ONLY: dict[str, object] = {
    "kanban.max_in_progress": 2,
    "tool_loop_guardrails.hard_stop_enabled": True,
    "agent.max_turns": 150,
    # 90 min: Hermes tells the agent to wrap up at 80% of it (agent/conversation_loop.py
    # _maybe_inject_run_budget_wrapup); a GotA build card waits about 55 min on its gates.
    "agent.run_budget_seconds": 5400,
    # Hermes clamps a process_manage wait to terminal.timeout (tools/process_registry.py
    # ProcessRegistry.wait) and uses it as the default foreground timeout. 1800 lets one wait
    # cover a long build or local check; the default 180 forced a model call every 3 minutes.
    "terminal.timeout": 1800,
}
UPGRADE_DEFAULTS: dict[str, tuple[object, object]] = {
    "agent.run_budget_seconds": (2700, 5400),  # shipped until 2026-10-08
    "terminal.timeout": (180, 1800),  # the Hermes default
}
UNSET = ("kanban.default_assignee",)
ENV_OVERRIDES = {"model.default": "OPENROUTER_MODEL"}

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


def pending(home: Path, env: Mapping[str, str] | None = None) -> list[list[str]]:
    """The ``hermes`` argv lists that would bring the profile config in line with C9;
    ``env`` supplies the ``ENV_OVERRIDES`` values."""
    config = read_config(home)
    commands = []
    for key, value in always(home).items():
        if _lookup(config, key) != value:
            commands.append(["config", "set", key, _cli_value(value)])
    for key, value in MISSING_ONLY.items():
        if _lookup(config, key) is _ABSENT:
            commands.append(["config", "set", key, _cli_value(value)])
    for key, (old, new) in UPGRADE_DEFAULTS.items():
        if _lookup(config, key) == old:
            commands.append(["config", "set", key, _cli_value(new)])
    for key in UNSET:
        if _lookup(config, key) not in (_ABSENT, None, ""):
            commands.append(["config", "unset", key])
    for key, name in ENV_OVERRIDES.items():
        value = (env or {}).get(name, "").strip()
        if value and _lookup(config, key) != value:
            commands.append(["config", "set", key, value])
    return commands


def apply(runner: Runner, home: Path, env: Mapping[str, str] | None = None) -> list[list[str]]:
    """Apply the C9 migrations to the profile at ``home``; return the commands run."""
    commands = pending(home, env)
    for argv in commands:
        runner(argv, Path(home))
    return commands
