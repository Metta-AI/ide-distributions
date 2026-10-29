"""``research softmax``: whoami prints the identity; exec sets the identity env, refuses only
identity-changing subcommands, and runs anything else (a raw ``coworld submit`` included)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from research_core import cli
from research_core.platform import cli as softmax_cli

SERVER = "https://example.test/api"
TOKEN = "cga_test_token"

STUB = """#!/bin/sh
echo "PROGRAM=$(basename "$0")"
echo "ARGS=$*"
echo "CONFIG=$SOFTMAX_CONFIG_DIR"
echo "API=$COGAMES_API_URL"
echo "GUIDANCE=$COWORLD_AGENT_GUIDANCE"
"""


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("IDE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("IDE_PLATFORM", "fake")
    monkeypatch.setenv("COGAMES_API_URL", SERVER)
    monkeypatch.setenv("SOFTMAX_PLAYER_CREDENTIAL", TOKEN)
    monkeypatch.delenv("SOFTMAX_USER_CREDENTIAL", raising=False)
    monkeypatch.setenv("SOFTMAX_CONFIG_DIR", str(tmp_path / "no-softmax-config"))
    return tmp_path


@pytest.fixture
def workspace_venv(env, monkeypatch):
    """A stand-in workspace environment whose ``bin/coworld`` and ``bin/softmax`` echo their env."""
    venv = env / "venv"
    (venv / "bin").mkdir(parents=True)
    for name in ("coworld", "softmax"):
        script = venv / "bin" / name
        script.write_text(STUB)
        os.chmod(script, 0o755)
    monkeypatch.setenv("IDE_WORKSPACE_ENV", str(venv))
    return venv


def test_whoami_prints_the_player_identity(env, capsys):
    assert cli.main(["softmax", "whoami"]) == 0
    who = json.loads(capsys.readouterr().out)
    assert who["identity"] == "player"
    assert who["kind"] == "player" and who["subject_id"] == "ply_fake"


def test_whoami_user_without_a_credential_fails_clearly(env, capsys):
    assert cli.main(["softmax", "whoami", "--identity", "user"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["ok"] is False and "User identity not provisioned" in error["error"]


def test_whoami_user_with_a_credential(env, monkeypatch, capsys):
    monkeypatch.setenv("SOFTMAX_USER_CREDENTIAL", "usr-token")
    assert cli.main(["softmax", "whoami", "--identity", "user"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "user"


@pytest.mark.parametrize(
    "command",
    [
        ["softmax", "login"],
        ["softmax", "logout"],
        ["softmax", "set-token", "abc"],
        ["softmax", "get-token"],
        ["softmax", "exchange-code", "xyz"],
        ["softmax", "player", "use", "ply_other"],
        ["softmax", "player", "unset"],
        ["softmax", "--verbose", "login"],
    ],
)
def test_exec_refuses_identity_changing_commands(env, workspace_venv, capsys, command):
    assert cli.main(["softmax", "exec", "--", *command]) == 2
    err = capsys.readouterr().err
    assert "refuses" in err and "SOFTMAX_PLAYER_CREDENTIAL" in err
    assert not (env / "ws" / ".state" / "softmax").exists()


def test_refused_reason_names_the_subcommand():
    assert "`softmax player use`" in softmax_cli.refused_reason(["softmax", "player", "use", "x"])
    assert softmax_cli.refused_reason(["softmax", "player", "list"]) is None
    assert softmax_cli.refused_reason(["coworld", "submit", "pv"]) is None
    assert softmax_cli.refused_reason(["softmax", "status"]) is None


def test_exec_runs_only_coworld_or_softmax(env, workspace_venv, capsys):
    assert cli.main(["softmax", "exec", "--", "ls"]) == 2
    assert "coworld or softmax" in capsys.readouterr().err
    assert cli.main(["softmax", "exec"]) == 2
    assert "usage" in capsys.readouterr().err


def test_exec_runs_the_cli_with_the_player_identity_env(env, workspace_venv, capfd):
    assert cli.main(["softmax", "exec", "--", "softmax", "status", "--json"]) == 0
    out = capfd.readouterr().out
    config_dir = env / "ws" / ".state" / "softmax"
    assert "PROGRAM=softmax" in out
    assert "ARGS=status --json" in out
    assert f"CONFIG={config_dir}" in out
    assert f"API={SERVER}" in out
    assert "GUIDANCE=0" in out
    credentials = yaml.safe_load((config_dir / "credentials.yaml").read_text())
    assert credentials["tokens"] == {SERVER: TOKEN}
    assert (config_dir / "credentials.yaml").stat().st_mode & 0o777 == 0o600


def test_exec_lets_a_raw_coworld_submit_run(env, workspace_venv, capfd):
    assert cli.main(["softmax", "exec", "--", "coworld", "submit", "pv_1", "--league", "l"]) == 0
    out = capfd.readouterr().out
    assert "PROGRAM=coworld" in out and "ARGS=submit pv_1 --league l" in out


def test_exec_returns_the_commands_exit_code(env, workspace_venv, capfd):
    (workspace_venv / "bin" / "coworld").write_text("#!/bin/sh\nexit 7\n")
    assert cli.main(["softmax", "exec", "--", "coworld", "credits"]) == 7


def test_exec_without_the_workspace_environment_names_prepare(env, capsys):
    assert cli.main(["softmax", "exec", "--", "coworld", "credits"]) == 2
    err = capsys.readouterr().err
    assert (
        "prepare.py" in err and str(Path(env / "ws" / "tools" / "venv" / "bin" / "coworld")) in err
    )


def test_exec_without_a_player_credential_is_refused(env, workspace_venv, monkeypatch, capsys):
    monkeypatch.delenv("SOFTMAX_PLAYER_CREDENTIAL")
    assert cli.main(["softmax", "exec", "--", "coworld", "credits"]) == 2
    assert "Player identity not provisioned" in capsys.readouterr().err


def test_softmax_without_a_subcommand_prints_usage(env, capsys):
    assert cli.main(["softmax"]) == 2
    assert "usage" in capsys.readouterr().err
