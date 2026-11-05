import re
from pathlib import Path

import yaml

from play import plugin

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
    assert "this only applies once the coworld comes up" in play_soul
    assert (
        "Container coworlds are not supported yet." in (PROFILES["builder"] / "SOUL.md").read_text()
    )


def test_every_name_the_soul_and_readme_quote_exists_in_the_code():
    # A backticked tool, field or error code that nothing in the source mentions is a promise the
    # model or a reader cannot act on (such as a tool that was removed).
    source = "\n".join(path.read_text() for path in (DISTRIBUTION / "src/play").glob("*.py"))
    soul = (DISTRIBUTION / "SOUL.md").read_text()
    # The language sections at the end of the SOUL name the games' own globals, not ours.
    documents = {"SOUL.md": soul[: soul.index("## GotA BASIC")]}
    documents["README.md"] = (DISTRIBUTION / "README.md").read_text()
    for document, text in documents.items():
        for name in re.findall(r"`([a-z]+(?:_[a-z]+)+)(?:[:`])", text):
            assert name in source, (document, name)


def test_ide_profiles_do_not_import_play():
    for folder in ("orchestrator", "researcher"):
        for path in (REPO / folder).rglob("*.py"):
            if ".venv" in path.parts:
                continue
            assert not re.search(r"^\s*(from|import) play\b", path.read_text(), re.MULTILINE), path


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


def test_play_restart_needs_an_explicit_yes_and_never_names_files():
    soul = (PROFILES["play"] / "SOUL.md").read_text()
    for needle in (
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
    assert "tone" in soul and "fresh_start" in soul and "`who_we_are`" not in soul


def test_play_cares_first_decides_small_things_recovers_and_learns():
    soul = (PROFILES["play"] / "SOUL.md").read_text()
    for needle in (
        "## Every conversation starts with them",
        "Decide small things yourself",
        "Getting stuck is yours to solve",
        "## Getting better with them",
        "do not ask them to choose",
        "once, on the first message",
        "at most twice",
        "never covers changing or uploading",
        "unless the user names another",
        "## Always a conversation",
        "picking up where you left off",
        "`league_status` (call it once",
        "Never invent any of it",
        "is the nod",
        "again only if the",
        "except when you talk about what something costs",
        "sentence or two (what you are about to do",
        "open-ended, costly, or a judgment call",
        "follow their lead",
        "inside work they have agreed to",
        "Spending credits is never a small thing",
        "before the first spend in a conversation",
        "within the amount and scope they",
        "Releasing always needs",
        "After any exchange where you learned something",
        "do not record sensitive personal details",
    ):
        assert needle in soul, needle
    assert soul.index("## Every conversation starts with them") < soul.index("## How the work goes")


def test_play_reads_the_live_league_through_league_status_only():
    soul = " ".join((PROFILES["play"] / "SOUL.md").read_text().split())
    for needle in (
        (
            "`league_status` (call it once at the start of the conversation, and again only if the "
            "user asks how it is going; it is read-only and free)"
        ),
        "If a source has nothing or is unavailable, say nothing about that item",
        "Never invent any of it",
    ):
        assert needle in soul, needle
    assert soul.count("league_status") == 2 - 1  # only in the conversation section
