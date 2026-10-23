"""Game scope: which game a command works on and where its folder, repository and clone are.

A host stands in as ``<tmp>/host`` with ``profiles/researcher-1`` and the default game's
canonical repository ``canonical-gota.git``; ``fakegame`` is the other game.
"""

import json
from pathlib import Path

import pytest

from ide_research_core import workspace
from ide_research_core.cli import main
from ide_research_core.games import scope
from ide_research_core.games.loader import GameError
from ide_research_core.io import gitops

DISTRIBUTION = Path(__file__).resolve().parents[3]


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "host"
    home = root / "profiles" / "researcher-1"
    home.mkdir(parents=True)
    return {
        "HERMES_HOME": str(home),
        "RESEARCH_GAME": "gota",
        "RESEARCH_CANONICAL": str(root / "canonical-gota.git"),
        "RESEARCH_WORKSPACE": str(home / "workspace"),
    }


def test_env_for_game_points_the_three_variables_and_keeps_the_budget(host):
    root = Path(host["HERMES_HOME"]).parent.parent
    scoped = scope.env_for_game(host, "fakegame")
    assert scoped["RESEARCH_GAME"] == "fakegame"
    assert scoped["RESEARCH_CANONICAL"] == str(root / "canonical-fakegame.git")
    assert scoped["RESEARCH_WORKSPACE"] == host["HERMES_HOME"] + "/workspace-fakegame"
    # One host-wide allowance: the budget stays in the default game's repository.
    assert scope.budget_path(scoped) == root / "canonical-gota.git" / "episode-budget.db"
    assert scope.env_for_game(host, None) == host
    assert scope.env_for_game(host, "gota") == host


def test_game_names_are_directory_names(host):
    for bad in ("../x", "Gota", "a/b", "", "-x", "x" * 41):
        with pytest.raises(GameError):
            scope.env_for_game(host, bad)


def test_host_root_is_above_profiles(host, tmp_path):
    assert scope.host_root(host) == tmp_path / "host"
    assert scope.host_root({"HERMES_HOME": str(tmp_path / "solo")}) == tmp_path / "solo"


def test_installed_games_are_found_after_shipped_ones(host):
    installed = scope.installed_games_dir(host)
    (installed / "newgame").mkdir(parents=True)
    (installed / "newgame" / "GAME.md").write_text("---\nname: newgame\n---\n")
    (installed / "fakegame").mkdir()
    (installed / "fakegame" / "GAME.md").write_text("---\nname: shadow\n---\n")
    assert scope.locate_game("newgame", host) == scope.host_root(host)
    assert scope.locate_game("fakegame", host) == DISTRIBUTION  # shipped wins
    with pytest.raises(GameError, match="not found"):
        scope.locate_game("absent", host)
    listed = {entry["name"]: entry for entry in scope.list_games(host)}
    assert listed["newgame"]["kind"] == "installed" and listed["fakegame"]["kind"] == "shipped"
    assert "_template" not in listed
    assert not listed["fakegame"]["workspace"]


def _game_clone(host, game):
    """The clone ``workspace bootstrap --game`` makes, and one card worktree of it."""
    env = scope.env_for_game(host, game)
    clone = Path(env["RESEARCH_WORKSPACE"])
    workspace.bootstrap(clone, Path(env["RESEARCH_CANONICAL"]), game, DISTRIBUTION)
    worktree = clone / ".worktrees" / "card-1"
    gitops.git(clone, "worktree", "add", "-q", "-b", "card/card-1", str(worktree), "HEAD")
    return clone, worktree


def test_a_worktree_names_its_game(host):
    clone, worktree = _game_clone(host, "fakegame")
    assert scope.game_of_clone(clone) == "fakegame"
    scoped = scope.env_for_worktree(host, worktree)
    assert scoped["RESEARCH_GAME"] == "fakegame"
    assert scoped["RESEARCH_WORKSPACE"] == str(clone)
    assert Path(scoped["RESEARCH_CANONICAL"]) == Path(host["RESEARCH_CANONICAL"]).parent / (
        "canonical-fakegame.git"
    )
    assert {entry["name"]: entry["workspace"] for entry in scope.list_games(host)}["fakegame"]


def test_a_worktree_of_the_default_game_or_no_clone_changes_nothing(host, tmp_path):
    gota = {**host, "RESEARCH_GAME": "fakegame"}
    _, worktree = _game_clone(host, "fakegame")
    assert scope.env_for_worktree(gota, worktree) == gota
    assert scope.env_for_worktree(host, tmp_path) == host


def test_the_cli_works_on_the_card_game_inside_a_worker(host, monkeypatch, capsys):
    _, worktree = _game_clone(host, "fakegame")
    for name, value in host.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(worktree))
    # main() sets these in os.environ; setting them here makes monkeypatch remove them after.
    for name in ("RESEARCH_EPISODE_BUDGET", "RESEARCH_PROFILE_GAME", "RESEARCH_PROFILE_CANONICAL",
                 "RESEARCH_PROFILE_WORKSPACE"):  # fmt: skip
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    assert main(["game", "show"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["name"] == "fakegame" and data["root"] == str(DISTRIBUTION)


def test_bootstrap_prepares_another_game_beside_the_default(host, monkeypatch, capsys):
    for name, value in host.items():
        monkeypatch.setenv(name, value)
    assert main(["workspace", "bootstrap", "--game", "fakegame"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    root = Path(host["HERMES_HOME"]).parent.parent
    assert data["game"] == "fakegame"
    assert data["canonical"] == str(root / "canonical-fakegame.git")
    assert data["workspace"] == host["HERMES_HOME"] + "/workspace-fakegame"
    assert (Path(data["workspace"]) / "strategy" / "STRATEGY.md").is_file()
    assert not (root / "canonical-gota.git").exists()  # the default game is untouched
    assert scope.installed_games_dir(host).is_dir()  # skills.external_dirs needs it
    assert main(["workspace", "bootstrap", "--game", "nope"]) == 1
    assert "not found" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_choosing_a_game_from_a_scoped_environment_uses_the_profile_paths(host):
    _, worktree = _game_clone(host, "fakegame")
    scoped = scope.env_for_worktree(host, worktree)
    back = scope.env_for_game(scoped, "gota")
    for name in ("RESEARCH_GAME", "RESEARCH_CANONICAL", "RESEARCH_WORKSPACE"):
        assert back[name] == host[name], name
    other = scope.env_for_game(scoped, "fakegame")
    assert other["RESEARCH_WORKSPACE"] == host["HERMES_HOME"] + "/workspace-fakegame"
    assert scope.budget_path(other) == scope.budget_path(host)


def test_returning_to_the_default_game_restores_a_default_workspace(host):
    profile = {key: value for key, value in host.items() if key != "RESEARCH_WORKSPACE"}
    other = scope.env_for_game(profile, "fakegame")
    back = scope.env_for_game(other, "gota")
    assert back["RESEARCH_WORKSPACE"] == host["HERMES_HOME"] + "/workspace"
    assert back["RESEARCH_CANONICAL"] == host["RESEARCH_CANONICAL"]
