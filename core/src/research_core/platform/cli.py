"""``research softmax``: the workspace's platform identity for a shell (design C.3).

``research softmax whoami [--identity player|user]`` prints the identity as JSON.

``research softmax exec -- <coworld|softmax> ...`` runs the official CLI from the workspace
environment as the Player: it writes ``credentials.yaml`` under ``<workspace>/.state/softmax``
from ``SOFTMAX_PLAYER_CREDENTIAL`` (``identity.write_credentials_dir``) and starts the command
with ``SOFTMAX_CONFIG_DIR`` pointing there, ``COGAMES_API_URL`` set and
``COWORLD_AGENT_GUIDANCE=0``. It refuses only the subcommands that would change that identity
(``softmax login|logout|set-token|get-token|exchange-code`` and ``softmax player use|unset``);
everything else passes through, a raw ``coworld submit`` included (the advisory hook notes it;
the ``upload`` and ``submit`` tools are the recorded path). The command's exit code is returned.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict

from research_core import layout

PROGRAMS = ("coworld", "softmax")
# Subcommands that would change the identity hosting provisioned; words without options.
IDENTITY_CHANGING: tuple[tuple[str, ...], ...] = (
    ("softmax", "login"),
    ("softmax", "logout"),
    ("softmax", "set-token"),
    ("softmax", "get-token"),
    ("softmax", "exchange-code"),
    ("softmax", "player", "use"),
    ("softmax", "player", "unset"),
)
CREDENTIALS_DIR = "softmax"  # under <workspace>/.state


def run_whoami(args: argparse.Namespace) -> int:
    from research_core.platform import from_env
    from research_core.platform.errors import PlatformError

    env = dict(os.environ)
    try:
        who = from_env(env).whoami(args.identity)
    except PlatformError as error:
        print(
            json.dumps({"ok": False, "identity": args.identity, **error.to_dict()}), file=sys.stderr
        )
        if error.status == 401:
            _report_login_failure(env, args.identity, error)
        return 1
    print(json.dumps({"identity": args.identity, **asdict(who)}))
    return 0


def run_exec(args: argparse.Namespace) -> int:
    from research_core.platform import identity
    from research_core.platform.errors import PlatformError

    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("usage: research softmax exec -- <coworld|softmax> ...", file=sys.stderr)
        return 2
    program = command[0]
    if program not in PROGRAMS:
        print(f"research softmax exec runs coworld or softmax, not {program!r}", file=sys.stderr)
        return 2
    refusal = refused_reason(command)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    env = dict(os.environ)
    workspace = layout.from_env(env)
    binary = workspace.venv / "bin" / program
    if not binary.exists():
        print(
            f"{binary} is missing: run the profile's scripts/prepare.py to build the workspace"
            " environment",
            file=sys.stderr,
        )
        return 2
    config_dir = workspace.state / CREDENTIALS_DIR
    try:
        identity.write_credentials_dir(env, config_dir)
    except PlatformError as error:
        print(f"cannot act as the Player: {error.detail}", file=sys.stderr)
        return 2
    child_env = {
        **env,
        "SOFTMAX_CONFIG_DIR": str(config_dir),
        "COGAMES_API_URL": identity.server(env),
        "COWORLD_AGENT_GUIDANCE": "0",
    }
    return subprocess.run([str(binary), *command[1:]], env=child_env, check=False).returncode


def refused_reason(command: list[str]) -> str | None:
    """Why ``command`` is refused, or None. Options are skipped when matching the words."""
    words = tuple(word for word in command if not word.startswith("-"))
    for prefix in IDENTITY_CHANGING:
        if words[: len(prefix)] == prefix:
            return (
                f"research softmax exec refuses `{' '.join(prefix)}`: it would change the"
                " workspace identity, which hosting provisions through SOFTMAX_PLAYER_CREDENTIAL;"
                " `research softmax whoami` shows the identity in use"
            )
    return None


def _report_login_failure(env: dict[str, str], identity: str, error: Exception) -> None:
    from research_core.platform.wait import report_login_failure
    from research_core.tools.base import ToolContext

    role = env.get("IDE_ROLE") or "orchestrator"
    report_login_failure(ToolContext.from_env(role, env), identity, error)  # type: ignore[arg-type]


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "softmax",
        help="The workspace's platform identity: whoami, or exec a coworld/softmax command as it",
    )
    commands = parser.add_subparsers(dest="softmax_command", metavar="<command>")
    whoami = commands.add_parser("whoami", help="Print the identity as JSON")
    whoami.add_argument("--identity", choices=["player", "user"], default="player")
    whoami.set_defaults(run=run_whoami)
    execute = commands.add_parser(
        "exec",
        help="Run a coworld or softmax command from the workspace environment as the Player",
        description=(
            "Writes the Player credentials for the official CLIs and runs the command with "
            "SOFTMAX_CONFIG_DIR, COGAMES_API_URL and COWORLD_AGENT_GUIDANCE=0 set. Refuses only "
            "identity-changing subcommands."
        ),
    )
    execute.add_argument("command", nargs=argparse.REMAINDER, help="-- <coworld|softmax> ...")
    execute.set_defaults(run=run_exec)
    parser.set_defaults(run=lambda _args: (parser.print_usage(sys.stderr), 2)[1])
