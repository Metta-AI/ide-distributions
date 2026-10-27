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
    play_soul = (PROFILES["play"] / "SOUL.md").read_text()
    assert "Coworlds you can't build for here" in play_soul
    assert "Copy prompt for your local agent" in play_soul
    assert "Not now" in play_soul
    assert "this only applies once the coworld comes up" in play_soul
    assert (
        "Container coworlds are not supported yet." in (PROFILES["builder"] / "SOUL.md").read_text()
    )


def test_gota_league_matches_the_game_contract():
    contract = frontmatter.load(REPO / "researcher/games/gota/GAME.md")
    assert GOTA.reference_league_id == contract["league_id"]


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


def test_play_lead_does_the_whole_loop_itself_in_plain_words():
    soul = (PROFILES["play"] / "SOUL.md").read_text()
    for needle in (
        "task_update",
        "Your mistakes are yours to fix",
        "Never ask the user to change a setting",
        "summary.not_measured",
        "watch_url",
        '"towerless v2"',
        "user_said_yes: true",
    ):
        assert needle in soul, needle
    steps = [
        soul.index(name)
        for name in (
            "program_show",
            "program_change",
            "program_upload",
            "practice_request",
            "practice_status",
            "program_release",
        )
    ]
    assert steps == sorted(steps)
    # No hand-off to another profile, and no internal words toward the user.
    for word in ("kanban_create", "builder", "max_runtime_seconds", "idempotency"):
        assert word not in soul, word


def test_play_restart_words_point_to_start_over_and_never_name_files():
    soul = (PROFILES["play"] / "SOUL.md").read_text()
    for needle in (
        '"Start over" action',
        "/play page",
        "Team testing",
        "explicit yes",
        "confirm: true",
    ):
        assert needle in soul, needle
    assert ".bas" not in soul
    assert 'say "your program"' in soul


def test_play_first_meeting_is_getting_to_know_each_other_not_game_talk():
    soul = (PROFILES["play"] / "SOUL.md").read_text()
    meeting = soul[
        soul.index("## The first meeting") : soul.index("Keep notes with the memory tool")
    ]
    for needle in (
        "getting to know each other",
        "how they like to talk",
        "expect of the",
        "concrete request",
    ):
        assert needle in meeting, needle
    assert "Do not fetch the starter in the first meeting" in meeting
    assert "On your first turn" not in soul and "plainly what you are for here" not in soul
    assert "tone" in soul and "fresh_start" in soul and "`who_we_are`" in soul
