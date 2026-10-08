"""Doctrine files (Plan 03 Task 4.1): rule IDs, code references, communication kinds."""

import re
from pathlib import Path

from autoresearch.comms.envelope import KINDS

ROOT = Path(__file__).resolve().parents[2]
DOCTRINE = ROOT / "doctrine"
RULE_LINE = re.compile(r"^(D-\d{2})\. ", re.MULTILINE)


def rule_ids() -> list[str]:
    return RULE_LINE.findall((DOCTRINE / "research-agent.md").read_text(encoding="utf-8"))


def test_rule_ids_are_unique_contiguous_and_ordered():
    assert rule_ids() == [f"D-{n:02d}" for n in range(1, 27)]


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
    assert used  # handoff.py uses D-23
    assert used <= set(rule_ids())


def test_communication_lists_exactly_the_outbound_kinds():
    text = (DOCTRINE / "communication.md").read_text(encoding="utf-8")
    section = text.split("## The five outbound kinds")[1].split("\n## ")[0]
    assert re.findall(r"^\| `(\w+)` \|", section, re.MULTILINE) == list(KINDS)


def test_communication_does_not_redefine_rule_ids():
    text = (DOCTRINE / "communication.md").read_text(encoding="utf-8")
    assert not RULE_LINE.findall(text)


def test_version_is_a_single_integer():
    assert re.fullmatch(r"\d+", (DOCTRINE / "VERSION").read_text(encoding="utf-8").strip())
