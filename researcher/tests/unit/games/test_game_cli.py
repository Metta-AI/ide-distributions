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


def test_list_names_each_games_league(capsys, monkeypatch):
    monkeypatch.delenv("RESEARCH_GAME", raising=False)
    assert main(["game", "list"]) == 0
    games = {game["name"]: game for game in json.loads(capsys.readouterr().out)["data"]["games"]}
    assert games["fakegame"]["league_id"] == "fake-league"
    assert games["pudge-wars-basic"]["league_id"] == "league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0"
