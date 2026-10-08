"""The README's shell commands lint: each executable exists and each ``$AUTORESEARCH_CLI``
command is real (Plan 03 Task 5.2)."""

import argparse
import json
import re
import shlex
import shutil
from pathlib import Path

import pytest

from autoresearch import cli

DISTRIBUTION = Path(__file__).resolve().parents[2]
README = (DISTRIBUTION / "README.md").read_text(encoding="utf-8")
SHELL_FENCE = re.compile(r"^```(?:sh|bash)\n(.*?)^```", re.MULTILINE | re.DOTALL)
BUILTINS = {"cd", "mkdir", "cp", "export", "set", "."}
OPTIONAL_TOOLS = {"nim"}  # skipped when absent from the machine
CLI_VARIABLE = "$AUTORESEARCH_CLI"


def shell_commands() -> list[list[str]]:
    commands = []
    for block in SHELL_FENCE.findall(README):
        joined = block.replace("\\\n", " ")
        for line in joined.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                for segment in line.strip("()").split(";"):  # subshell and `a; b` lines
                    words = shlex.split(segment)
                    while words and re.fullmatch(r"[A-Z_]+=\S*", words[0]):
                        words.pop(0)  # leading VAR=value assignments
                    if words:
                        commands.append(words)
    return commands


def cli_commands() -> dict[str, list[str]]:
    parser = cli.build_parser()
    groups = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    result = {}
    for name, group in groups.choices.items():
        subs = [a for a in group._actions if isinstance(a, argparse._SubParsersAction)]
        result[name] = list(subs[0].choices) if subs else []
    return result


def test_readme_has_shell_commands():
    assert len(shell_commands()) > 10


@pytest.mark.parametrize("command", shell_commands(), ids=lambda c: " ".join(c)[:60])
def test_command_executable_exists(command):
    executable = command[0]
    if executable == CLI_VARIABLE:
        return  # resolved by the next test
    if executable in BUILTINS:
        return
    if executable in OPTIONAL_TOOLS and shutil.which(executable) is None:
        pytest.skip(f"{executable} not installed")
    assert shutil.which(executable), f"{executable} is not on PATH"


def test_every_cli_command_in_the_readme_exists():
    available = cli_commands()
    used = [c[1:3] for c in shell_commands() if c[0] == CLI_VARIABLE]
    assert used, "README shows no CLI commands"
    for group, subcommand in used:
        assert subcommand in available.get(group, []), f"{group} {subcommand}"


def test_cron_jobs_in_the_readme_are_shipped_jobs():
    shipped = {job["id"] for job in json.loads((DISTRIBUTION / "cron/jobs.json").read_text())["jobs"]}
    named = {
        c[c.index("resume") + 1]
        for c in shell_commands()
        if c[0] == "hermes" and "resume" in c and c[-1] != "resume"
    }
    assert named == shipped


OPERATOR_SETUP = 'set -a; . "$PROFILE/.env"; set +a; export HERMES_HOME="$PROFILE"'


def test_operator_shell_setup_precedes_cli_examples():
    assert OPERATOR_SETUP in README
    assert README.index(OPERATOR_SETUP) < README.index('"' + CLI_VARIABLE + '" ')
    assert 'export PROFILE="' in README
    assert README.index('export PROFILE="') < README.index(OPERATOR_SETUP)


def test_bootstrap_commands_load_the_profile_env():
    lines = [line for line in README.splitlines() if "scripts/queue_keeper.py" in line]
    assert lines
    for line in lines:
        assert '. "$PROFILE/.env"' in line, line
