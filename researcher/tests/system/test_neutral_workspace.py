"""Exercise neutral and explicitly selected research through the real CLI and Git."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("repository", ["canonical.git", "canonical-gota.git"])
def test_game_scope_is_selected_by_the_task(tmp_path, repository):
    profile = tmp_path / "profiles/researcher-1"
    profile.mkdir(parents=True)
    clone = profile / "workspace"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RESEARCH_", "HERMES_"))
    } | {
        "HERMES_HOME": str(profile),
        "RESEARCH_CANONICAL": str(tmp_path / repository),
        "RESEARCH_WORKSPACE": str(clone),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }

    def cli(*args, worker=None):
        result = subprocess.run(
            [sys.executable, "-m", "ide_research_core", *args],
            env=env | ({"HERMES_KANBAN_WORKSPACE": str(worker)} if worker else {}),
            capture_output=True,
            text=True,
            check=True,
        )
        envelope = json.loads(result.stdout)
        assert envelope["ok"], envelope
        return envelope["data"]

    assert cli("workspace", "bootstrap")["game"] is None
    assert {path.name for path in clone.iterdir()} == {".git", ".gitignore", "AGENTS.md"}
    saved = clone / "user-work.txt"
    saved.write_text("Keep this research and its history.")
    assert cli("workspace", "bootstrap")["game"] is None
    assert saved.read_text() == "Keep this research and its history."
    assert cli("promotion", "pending") is None
    assert cli("game", "list")["default"] is None
    card = tmp_path / "neutral-card"
    subprocess.run(
        ["git", "-C", str(clone), "worktree", "add", "-b", "card/neutral", str(card)], check=True
    )
    assert cli("game", "list", worker=card)["default"] is None
    selected = cli("workspace", "bootstrap", "--game", "gota")
    assert selected["game"] == "gota"
    assert selected["workspace"] == str(profile / "workspace-gota")
    assert cli("game", "show", "--name", "gota")["name"] == "gota"
    assert cli("game", "list", worker=profile / "workspace-gota")["default"] == "gota"
    assert cli("game", "list")["default"] is None
