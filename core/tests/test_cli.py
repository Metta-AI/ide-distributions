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
