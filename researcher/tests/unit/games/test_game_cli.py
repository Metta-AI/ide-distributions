"""``autoresearch game show``."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("returncode", [0, 1])
def test_container_build_only_emits_publishable_receipt_on_success(
    tmp_path, monkeypatch, capsys, returncode
):
    from ide_research_core.games import cli

    context = tmp_path / "player"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\nCOPY player.py /player.py\n")
    (context / "player.py").write_text("print('player')\n")
    strategy = tmp_path / "STRATEGY.md"
    strategy.write_text("Play the supported starter")
    output = tmp_path / "build"
    monkeypatch.setenv("BUILDKIT_HOST", "tcp://127.0.0.1:1234")
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)
    monkeypatch.setattr(cli.gitops, "head", lambda path: "a" * 40)
    monkeypatch.setattr(cli.gitops, "git", lambda *args: str(tmp_path))

    def build(argv, **kwargs):
        assert argv[1:3] == ["build-policy", str(context)]
        assert "--dockerfile" in argv
        Path(argv[argv.index("--output") + 1]).write_bytes(b"archive")
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr(cli.subprocess, "run", build)
    code = main(
        [
            "game",
            "build-container",
            "--context",
            str(context),
            "--strategy",
            str(strategy),
            "--out",
            str(output),
        ]
    )
    result = json.loads(capsys.readouterr().out)
    if returncode:
        assert code == 1 and result["error"]["reason"] == "build_failed"
        assert not (output / "version.json").exists()
    else:
        assert code == 0
        receipt = json.loads((output / "version.json").read_text())
        assert receipt["source_path"] == "player/Dockerfile"
        assert set(receipt["source_hashes"]) == {
            "player/Dockerfile",
            "player/player.py",
            "STRATEGY.md",
        }
        assert receipt["artifact_type"] == "oci-image"
        assert receipt["policy_sha256"] == hashlib.sha256(b"archive").hexdigest()
        assert receipt["strategy_sha256"] == hashlib.sha256(strategy.read_bytes()).hexdigest()
        assert receipt["evaluation"] == {"status": "not_run"}


def test_container_manifest_includes_nested_clone_and_ignored_inputs(tmp_path):
    from ide_research_core.games.cli import container_sources

    context = tmp_path / "player"
    context.mkdir()
    (context / ".git").mkdir()
    (context / ".git/config").write_text("not source")
    (context / ".gitignore").write_text("generated.py\n")
    (context / ".dockerignore").write_text(".git\n")
    (context / "generated.py").write_text("generated source")
    strategy = tmp_path / "STRATEGY.md"
    strategy.write_text("strategy")
    before = container_sources(tmp_path, context, strategy)
    assert set(before) == {
        "STRATEGY.md",
        "player/.gitignore",
        "player/.dockerignore",
        "player/generated.py",
    }
    (context / "new.py").write_text("new input")
    assert container_sources(tmp_path, context, strategy) != before
    (context / "new.py").unlink()
    (context / "generated.py").unlink()
    assert container_sources(tmp_path, context, strategy) != before


def test_container_manifest_honors_dockerfile_specific_ignore_and_negation(tmp_path):
    from ide_research_core.games.cli import container_sources

    context = tmp_path / "player"
    context.mkdir()
    (context / "Dockerfile.test").write_text("FROM scratch\nCOPY . /\n")
    (context / ".dockerignore").write_text("keep.py\n")
    (context / "Dockerfile.test.dockerignore").write_text(".venv\n*.py\n!keep.py\n")
    (context / ".venv").mkdir()
    (context / ".venv/python").symlink_to("/not-a-source")
    (context / "keep.py").write_text("kept")
    (context / "skip.py").write_text("not built")
    strategy = tmp_path / "STRATEGY.md"
    strategy.write_text("strategy")
    manifest = container_sources(tmp_path, context, strategy, "Dockerfile.test")
    assert "player/keep.py" in manifest
    assert "player/skip.py" not in manifest
    assert not any(".venv" in path for path in manifest)
