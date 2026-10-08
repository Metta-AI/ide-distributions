"""Core skill conformance (Plan 03 Task 4.3).

Every core skill under ``skills/autoresearch/`` must:

- have valid Hermes front matter (``name`` equal to its directory, a ``description`` that
  begins "Use when" and fits Hermes's 60-character skill-index budget,
  ``agent/skill_utils.py:763`` at v0.21.5) and a body;
- name only CLI commands and flags that exist (checked against each parser's ``--help``);
- call the CLI as ``"$AUTORESEARCH_CLI"``, never as bare ``autoresearch``;
- never name a game;
- cite only doctrine IDs that exist.

The set of skills is System Design §6.1 plus the two maintenance skills the plan adds.
"""

import argparse
import re
from pathlib import Path

import frontmatter
import pytest

from autoresearch.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills" / "autoresearch"
RULE_LINE = re.compile(r"^(D-\d{2})\. ", re.MULTILINE)

# System Design §6.1, the skills table.
SECTION_6_1 = {
    "complete-card",
    "queue",
    "replay-diagnosis",
    "diagnose",
    "league-watch",
    "opponent-profile",
    "meta-recon",
    "experiment",
    "build-upload",
    "xp-request",
    "fetch-artifacts",
    "review-xp",
    "ab-compare",
    "submit",
    "lessons",
    "communicate",
    "groom-queue",
}
# Plan 03 Task 4.2 adds these for System Design §5.6 and C10.
PLAN_EXTRAS = {"kb-maintenance", "calibrate-noise"}

NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")  # Hermes skill_manager_tool.VALID_NAME_RE
DESCRIPTION_LIMIT = 60  # Hermes SKILL_PROMPT_DESC_LIMIT: longer descriptions are truncated
# A CLI call: the quoted variable, then words up to the end of the line, an inline-code
# backtick, or a shell operator. Backslash-newline continues a line.
CLI_CALL = re.compile(r'"\$AUTORESEARCH_CLI"((?:\\\n|[^`\n|;&>)])*)')
GROUPS = ("records", "stats", "kb", "card", "comms", "queue", "promotion", "softmax")
BARE_CALL = re.compile(rf"(?<![\w/$.-])(?:python3? -m )?autoresearch (?:{'|'.join(GROUPS)})\b")


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def skill_ids() -> list[str]:
    return [path.parent.name for path in skill_files()]


def rule_ids() -> set[str]:
    text = (ROOT / "doctrine" / "research-agent.md").read_text(encoding="utf-8")
    return set(RULE_LINE.findall(text))


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def resolve(words: list[str]) -> tuple[list[str], argparse.ArgumentParser, list[str]]:
    """Walk the parser tree along ``words``: (command path, leaf parser, remaining words)."""
    parser, path = build_parser(), []
    while words and (children := subcommands(parser)):
        if words[0] not in children:
            where = " ".join(path) or "top level"
            raise AssertionError(f"{where}: no command {words[0]!r}\n{parser.format_help()}")
        path.append(words[0])
        parser = children[words.pop(0)]
    if subcommands(parser):
        raise AssertionError(f"{' '.join(path)} needs a subcommand")
    return path, parser, words


def cli_calls(text: str) -> list[str]:
    return [match.group(1).replace("\\\n", " ") for match in CLI_CALL.finditer(text)]


@pytest.fixture(params=skill_files(), ids=skill_ids())
def skill(request) -> Path:
    return request.param


def test_every_core_skill_has_a_file():
    assert set(skill_ids()) == SECTION_6_1 | PLAN_EXTRAS


def test_front_matter_is_valid(skill):
    post = frontmatter.loads(skill.read_text(encoding="utf-8"))
    name, description = post.metadata.get("name"), post.metadata.get("description")
    assert name == skill.parent.name
    assert NAME_PATTERN.fullmatch(name)
    assert isinstance(description, str) and description.startswith("Use when ")
    assert len(description) <= DESCRIPTION_LIMIT, description
    assert post.content.strip()


def test_names_its_system_design_procedure(skill):
    assert re.search(r"System Design §5\.\d+", skill.read_text(encoding="utf-8"))


def test_cli_commands_and_flags_exist(skill):
    calls = cli_calls(skill.read_text(encoding="utf-8"))
    for call in calls:
        words = call.split()
        if not words:  # the variable named in prose, not a call
            continue
        path, leaf, rest = resolve(list(words))
        if len(path) > 1:  # the command appears in its parent's --help
            parent = build_parser()
            for word in path[:-1]:
                parent = subcommands(parent)[word]
            assert path[-1] in parent.format_help(), f"{call!r}: {path[-1]} not in --help"
        leaf_help = leaf.format_help()
        for word in rest:
            if word.startswith("--"):
                flag = word.split("=", 1)[0]
                assert re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", leaf_help), (
                    f"{skill.parent.name}: {' '.join(path)} has no flag {flag}\n{leaf_help}"
                )


def test_cli_calls_parse_on_the_test_helpers():
    calls = cli_calls(
        'Run `"$AUTORESEARCH_CLI" card finish --outcome done` and\n'
        '"$AUTORESEARCH_CLI" queue request drop Q-1 \\\n  --reason "x" | jq .'
    )
    assert [call.split()[:3] for call in calls] == [
        ["card", "finish", "--outcome"],
        ["queue", "request", "drop"],
    ]
    assert "--reason" in calls[1]
    with pytest.raises(AssertionError):
        resolve(["queue", "nosuch"])
    assert BARE_CALL.search("run autoresearch records new")
    assert not BARE_CALL.search('"$AUTORESEARCH_CLI" records new')


def test_no_bare_autoresearch_call(skill):
    for number, line in enumerate(skill.read_text(encoding="utf-8").splitlines(), start=1):
        assert not BARE_CALL.search(line), f"{skill.parent.name}:{number}: {line}"


def test_core_skills_never_name_a_game(skill):
    assert not re.search(
        r"gota|gods of the arena", skill.read_text(encoding="utf-8"), re.IGNORECASE
    )


def test_cited_doctrine_ids_exist(skill):
    cited = set(re.findall(r"\bD-\d{2}\b", skill.read_text(encoding="utf-8")))
    assert cited, f"{skill.parent.name} cites no doctrine rule"
    assert cited <= rule_ids(), cited - rule_ids()
