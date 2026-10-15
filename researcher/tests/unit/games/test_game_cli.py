"""``autoresearch game show``."""

import json

from ide_research_core.cli import main


def show(capsys, *argv):
    code = main(["game", "show", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_show_named_game(capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_GAME", raising=False)
    code, env = show(capsys, "--name", "fakegame")
    assert code == 0
    data = env["data"]
    assert data["league_id"] == "fake-league"
    assert data["bindings"]["evaluate"] == "python3 games/fakegame/tools/fake_eval.py"
    assert data["outcome_metrics"] == [
        {"name": "score", "direction": "up"},
        {"name": "errors", "direction": "down"},
    ]
    assert data["performance_guardrails"] == ["score"]
    assert data["min_episodes_floor"] == 10 and data["layers"] == ["parameters"]
    assert "guide" not in data and data["path"].endswith("games/fakegame/GAME.md")


def test_show_defaults_to_the_profile_game(capsys, monkeypatch):
    monkeypatch.setenv("RESEARCH_GAME", "fakegame")
    code, env = show(capsys)
    assert code == 0 and env["data"]["name"] == "fakegame"


def test_unknown_or_unnamed_game(capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_GAME", raising=False)
    assert show(capsys)[0] == 2
    code, env = show(capsys, "--name", "nope")
    assert code == 1 and "not found" in env["error"]["message"]
