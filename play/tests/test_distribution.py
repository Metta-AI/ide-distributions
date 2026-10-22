import re
import shutil
import subprocess
import sys
from pathlib import Path

import frontmatter
import yaml

from play import plugin
from play.games import GOTA

DISTRIBUTION = Path(__file__).resolve().parents[1]
REPO = DISTRIBUTION.parent
PROFILES = {"play": DISTRIBUTION, "builder": DISTRIBUTION / "builder"}
RESEARCH_WORDS = re.compile(
    r"hypothes|experiment|champion|\bkb\b|knowledge base|research|promot|ontology", re.IGNORECASE
)


def test_both_profiles_are_installable_distributions():
    for name, folder in PROFILES.items():
        manifest = yaml.safe_load((folder / "distribution.yaml").read_text())
        assert manifest["name"] == name
        assert (folder / "SOUL.md").is_file() and (folder / "config.yaml").is_file()
    builder = yaml.safe_load((PROFILES["builder"] / "config.yaml").read_text())
    assert "plugins" not in builder
    assert "coworld submit*" in builder["approvals"]["deny"]


def test_no_research_machinery_mode_flags_or_chat_commands():
    texts = {
        f"{name}/SOUL.md": (folder / "SOUL.md").read_text() for name, folder in PROFILES.items()
    }
    texts |= {
        f"tool {name}": json
        for name, json in ((name, str(plugin.schema(name))) for name in plugin.TOOLS)
    }
    for where, text in texts.items():
        assert not RESEARCH_WORDS.search(text), (where, RESEARCH_WORDS.search(text))
        assert "SOFTMAX_COGENT_MODE" not in text and not re.search(r"(?m)^\s*/[a-z]", text)
    for soul in (folder / "SOUL.md" for folder in PROFILES.values()):
        assert "Container coworlds are not supported yet." in soul.read_text()


def test_gota_league_matches_the_game_contract():
    contract = frontmatter.load(REPO / "researcher/games/gota/GAME.md")
    assert GOTA.league_id == contract["league_id"]


def test_ide_profiles_do_not_import_play():
    for folder in ("orchestrator", "researcher"):
        for path in (REPO / folder).rglob("*.py"):
            if ".venv" in path.parts:
                continue
            assert not re.search(r"^\s*(from|import) play\b", path.read_text(), re.MULTILINE), path


def test_sync_regenerates_every_copy_identically(tmp_path):
    """The committed copies, the IDE profiles' included, are what sync_shared.py makes."""
    for name in ("shared", "ontology", "tools"):
        shutil.copytree(REPO / name, tmp_path / name)
    ignore = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache")
    for name in ("researcher", "orchestrator", "play"):
        shutil.copytree(REPO / name, tmp_path / name, ignore=ignore)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "tools/sync_shared.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "copies are up to date"
    assert not (tmp_path / "play/SOUL.md").read_text().count("ontology")


def test_play_lead_continues_the_loop_after_the_builder_card():
    soul = (PROFILES["play"] / "SOUL.md").read_text()
    for needle in (
        "$PLAY_WORKSPACE",
        "word for word",
        "ready to upload",
        "subscribed: true",
        "[kanban] Task",
        "continue on your own",
        "practice_status",
        "program_release",
    ):
        assert needle in soul, needle
    steps = [soul.index(name) for name in ("kanban_show", "program_upload", "practice_request", "practice_status")]
    assert steps == sorted(steps)
