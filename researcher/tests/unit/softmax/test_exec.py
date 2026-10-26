"""``research softmax exec``: the real CLIs as the Player, from an agent's shell."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from ide_research_core.cli import main
from ide_research_core.softmax_tool.platform_cli import (
    DEFAULT_SERVER,
    ExecRefused,
    PlatformCli,
    ToolMissing,
    exec_problem,
)

from .conftest import AGENT_TOKEN

RESEARCHER = Path(__file__).resolve().parents[3]


class Recorder:
    """A ``subprocess.run`` stand-in that records each call and returns a set exit code."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode)


def make(tmp_path, runner, **env) -> PlatformCli:
    return PlatformCli(
        {"HERMES_HOME": str(tmp_path), "SOFTMAX_PLAYER_CREDENTIAL": AGENT_TOKEN, **env},
        run=runner,
        bin_dir=tmp_path / "bin",
    )


def caller_env(tmp_path) -> dict[str, str]:
    return {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "TMPDIR": "/data/tmp",
        "SOFTMAX_PLAYER_CREDENTIAL": AGENT_TOKEN,
        "SOFTMAX_CONFIG_DIR": "/home/agent/.softmax",
    }


def test_exec_runs_the_venv_cli_as_the_player_in_the_callers_directory(tmp_path):
    runner = Recorder(returncode=7)
    cli = make(tmp_path, runner)
    work = tmp_path / "worktree"
    code = cli.exec("coworld", ["leagues", "--json"], cwd=work, caller_env=caller_env(tmp_path))
    assert code == 7  # the child's exit code
    (argv, kwargs), = runner.calls
    assert argv == [str(tmp_path / "bin" / "coworld"), "leagues", "--json"]  # default server
    assert kwargs["cwd"] == str(work)
    assert "capture_output" not in kwargs and "stdout" not in kwargs  # stdio passes through
    env = kwargs["env"]
    assert "SOFTMAX_PLAYER_CREDENTIAL" not in env
    assert env["SOFTMAX_CONFIG_DIR"] == str(cli.credentials_path.parent)
    assert env["COWORLD_AGENT_GUIDANCE"] == "0"
    assert env["HOME"] == "/home/agent" and env["TMPDIR"] == "/data/tmp"
    assert AGENT_TOKEN not in " ".join(argv) and AGENT_TOKEN not in json.dumps(
        {k: v for k, v in env.items()}
    )
    written = yaml.safe_load(cli.credentials_path.read_text())
    assert written["tokens"] == {DEFAULT_SERVER: AGENT_TOKEN}


def test_exec_names_a_non_default_server(tmp_path):
    runner = Recorder()
    cli = make(tmp_path, runner, COGAMES_API_URL="https://staging.softmax.com/api/")
    cli.exec("coworld", ["leagues"], cwd=tmp_path, caller_env={})
    (argv, kwargs), = runner.calls
    assert argv[-2:] == ["--server", "https://staging.softmax.com/api"]
    assert kwargs["env"]["COGAMES_API_URL"] == "https://staging.softmax.com/api"


@pytest.mark.parametrize(
    "tool, args",
    [
        ("softmax", ["login"]),
        ("softmax", ["logout"]),
        ("softmax", ["get-login-url"]),
        ("softmax", ["exchange-code", "abc"]),
        ("softmax", ["set-token", "x"]),
        ("softmax", ["get-token"]),
        ("softmax", ["player", "use", "ply_1"]),
        ("softmax", ["player", "unset"]),
        ("coworld", ["player", "use", "ply_1"]),
        ("coworld", ["player", "unset"]),
        ("coworld", ["leagues", "--server", "https://elsewhere"]),
        ("coworld", ["leagues", "--server=https://elsewhere"]),
        ("python3", ["-c", "print(1)"]),
        ("/usr/bin/coworld", ["leagues"]),
    ],
)
def test_exec_refuses_identity_changes_other_servers_and_other_programs(tmp_path, tool, args):
    runner = Recorder()
    with pytest.raises(ExecRefused):
        make(tmp_path, runner).exec(tool, args, cwd=tmp_path, caller_env={})
    assert runner.calls == []


def test_exec_allows_reads_and_player_listing():
    assert exec_problem("coworld", ["player", "list", "--json"]) is None
    assert exec_problem("softmax", ["status"]) is None
    assert exec_problem("coworld", ["docs", "--local"]) is None


def test_exec_maps_a_missing_executable(tmp_path):
    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    with pytest.raises(ToolMissing):
        make(tmp_path, missing).exec("coworld", ["leagues"], cwd=tmp_path, caller_env={})


def _cli(monkeypatch, tmp_path, *argv):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SOFTMAX_PLAYER_CREDENTIAL", AGENT_TOKEN)
    monkeypatch.delenv("RESEARCH_SOFTMAX", raising=False)
    runner = Recorder(returncode=4)
    real_init = PlatformCli.__init__

    def recording(self, env, run=subprocess.run, bin_dir=None, http_get=None):
        real_init(self, env, run=runner, bin_dir=tmp_path / "bin", http_get=http_get)

    monkeypatch.setattr(PlatformCli, "__init__", recording)
    return main(["softmax", "exec", *argv]), runner


def test_cli_exec_passes_arguments_after_the_separator_and_returns_the_exit_code(
    monkeypatch, tmp_path, capsys
):
    code, runner = _cli(monkeypatch, tmp_path, "--", "coworld", "episodes", "--json", "-n", "5")
    assert code == 4
    (argv, _), = runner.calls
    assert argv[1:] == ["episodes", "--json", "-n", "5"]
    assert capsys.readouterr().out == ""  # no envelope: the child's output is the output


def test_cli_exec_refusal_prints_the_envelope(monkeypatch, tmp_path, capsys):
    code, runner = _cli(monkeypatch, tmp_path, "--", "softmax", "get-token")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload["error"]["reason"] == "refused" and runner.calls == []
    assert AGENT_TOKEN not in json.dumps(payload)


def test_cli_exec_refuses_the_fake_backend(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("RESEARCH_SOFTMAX", "fake")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    code = main(["softmax", "exec", "--", "coworld", "leagues"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"]["reason"] == "fake_backend"


def test_cli_exec_without_a_command_is_bad_arguments(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert main(["softmax", "exec"]) == 2


# Hermes matches approvals.deny globs against the command from each executable position
# (v0.21.6 tools/approval_detection.py _deny_command_variants; approval_floors.py uses
# fnmatchcase on the lowercased variant). For ``"$RESEARCH_CLI" softmax exec -- coworld submit``
# the executable is the research CLI, so ``coworld submit*`` never sees ``coworld`` first:
# the researcher's deny list needs exec-shaped globs for the commands it reserves.
RESERVED_THROUGH_EXEC = (
    "coworld submit x --league l",
    "coworld upload-policy --name p",
    "coworld xp-request create body.json",
)


@pytest.mark.parametrize("command", RESERVED_THROUGH_EXEC)
def test_researcher_deny_list_blocks_reserved_commands_through_exec(command):
    import fnmatch

    deny = yaml.safe_load((RESEARCHER / "config.yaml").read_text())["approvals"]["deny"]
    for form in (
        f'"$RESEARCH_CLI" softmax exec -- {command}',
        f"research softmax exec {command}",
    ):
        variants = (form.lower(), form.replace('"', "").lower())
        assert any(fnmatch.fnmatchcase(v, p.lower()) for v in variants for p in deny), form
