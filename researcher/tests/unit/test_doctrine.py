"""File-only checks for stable research doctrine and the native worker lifecycle."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTRINE = ROOT / "doctrine"
RULE_LINE = re.compile(r"^(D-\d{2})\. ", re.MULTILINE)


def rule_ids() -> list[str]:
    return RULE_LINE.findall((DOCTRINE / "research-agent.md").read_text(encoding="utf-8"))


def test_rule_ids_are_unique_contiguous_and_ordered():
    assert rule_ids() == [f"D-{n:02d}" for n in range(1, 28)]


def test_groups_hold_the_rules_in_order():
    text = (DOCTRINE / "research-agent.md").read_text(encoding="utf-8")
    groups = re.findall(r"^## (.+)$", text, re.MULTILINE)
    assert groups == [
        "Authority and autonomy",
        "Evidence",
        "Experiment discipline",
        "Session contract",
        "Knowledge hygiene",
        "Communication",
        "Cards and the shared workspace",
    ]


def test_every_rule_code_used_in_src_exists():
    used = set()
    for path in (ROOT / "src").rglob("*.py"):
        used |= set(re.findall(r"\bD-\d{2}\b", path.read_text(encoding="utf-8")))
    assert used <= set(rule_ids())


def test_tool_fault_rule_preserves_work_and_blocks():
    text = (DOCTRINE / "research-agent.md").read_text(encoding="utf-8")
    rule = text[text.index("\nD-26.") : text.index("\nD-27.")]
    for required in (
        "help_request:",
        "kanban_block",
        "Preserve failed reports",
        "Do not change installed game tools",
    ):
        assert required in rule


def test_worker_roles_and_communication_channels():
    text = (DOCTRINE / "communication.md").read_text(encoding="utf-8")
    soul = (ROOT / "SOUL.md").read_text(encoding="utf-8")
    assert not (DOCTRINE / "coach-chat.md").exists()
    for prefix in ("note:", "question:", "suggestion:", "help_request:", "highlight:"):
        assert prefix in text and prefix in soul
    for required in ("kanban_comment", "own card", "user authority", "kanban_complete"):
        assert required in text
    for path in [ROOT / "SOUL.md", *DOCTRINE.glob("*.md")]:
        content = path.read_text(encoding="utf-8")
        for obsolete in (
            "AUTORESEARCH_",
            "outbox",
            "comms/inbox",
            "queue-keeper",
            "xp-poll",
            "card finish",
        ):
            assert obsolete not in content, (path, obsolete)


def test_communication_does_not_redefine_rule_ids():
    text = (DOCTRINE / "communication.md").read_text(encoding="utf-8")
    assert not RULE_LINE.findall(text)


def test_version_is_a_single_integer():
    assert re.fullmatch(r"\d+", (DOCTRINE / "VERSION").read_text(encoding="utf-8").strip())


def test_version_bumped_for_native_card_contract():
    assert int((DOCTRINE / "VERSION").read_text(encoding="utf-8")) >= 12
