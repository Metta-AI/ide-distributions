import pytest

from research_core import cli


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == "research 0.1.0"


def test_cli_no_command_exits_2(capsys):
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_cli_unknown_command_exits_2():
    with pytest.raises(SystemExit) as info:
        cli.main(["no-such-command"])
    assert info.value.code == 2


def test_tool_command_runs_a_tool(capsys, tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("IDE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("IDE_ROLE", "researcher")
    assert cli.main(["tool", "no-such-tool", "--args", "{}"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is False and "unknown tool" in out["error"]
    assert cli.main(["tool", "x", "--args", "{bad"]) == 0
    assert "not JSON" in capsys.readouterr().out
