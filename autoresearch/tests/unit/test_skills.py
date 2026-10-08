"""Core and GotA skill conformance (Plan 03 Task 4.3, Plan 02 Task 1.5).

Every core skill under ``skills/autoresearch/`` must:

- have valid Hermes front matter (``name`` equal to its directory, a ``description`` that
  begins "Use when" and fits Hermes's 60-character skill-index budget,
  ``agent/skill_utils.py:763`` at v0.21.5) and a body;
- name only CLI commands and flags that exist: every ``"$AUTORESEARCH_CLI" ...`` call, and
  every inline-code span that starts with a CLI group name (such as ``stats design``), resolves
  to a leaf parser, and every ``--flag`` is one of that parser's option strings;
- call the CLI as ``"$AUTORESEARCH_CLI"``, never as bare ``autoresearch``;
- never name a game;
- cite only doctrine IDs that exist.

The core set is System Design §6.1 plus two maintenance skills. GotA bindings share
metadata, doctrine and CLI checks; only core skills must avoid game names. Game
instruments are checked through whitelisted --help paths without executing actions.
"""

import argparse
import re
import subprocess
import sys
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
# backtick, or a shell operator. Backslash-newline continues a line. ``>`` ends a call only
# after a space (a redirect), so placeholders such as ``<E-id>`` stay inside the call.
CLI_CALL = re.compile(r'"\$AUTORESEARCH_CLI"((?:\\\n|[^`\n|;&>)]|(?<! )>)*)')
# A single-backtick inline-code span (not a fence); it may wrap across lines.
INLINE_CODE = re.compile(r"(?<!`)`(?!`)([^`]+?)`(?!`)")
GROUPS = ("records", "stats", "kb", "card", "comms", "queue", "promotion", "softmax", "game")
# An inline-code span naming a command without the variable: a group, then a subcommand.
PREFIXLESS = re.compile(rf"^(?:{'|'.join(GROUPS)}) [a-z]")
BARE_CALL = re.compile(rf"(?<![\w/$.-])(?:python3? -m )?autoresearch (?:{'|'.join(GROUPS)})\b")


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md")) + sorted((ROOT / "skills" / "gota").glob("*/SKILL.md"))


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


def _unwrap_inline_code(text: str) -> str:
    """Join inline-code spans that wrap across lines, so a wrapped call reads as one line."""
    return INLINE_CODE.sub(lambda m: "`" + " ".join(m.group(1).split("\n")) + "`", text)


def cli_calls(text: str) -> list[str]:
    """Every CLI call: ``"$AUTORESEARCH_CLI" ...`` ones, then inline-code spans that start
    with a group name (such as ``stats design <E-id>``), as the words after the program."""
    text = _unwrap_inline_code(text)
    calls = [match.group(1).replace("\\\n", " ") for match in CLI_CALL.finditer(text)]
    for match in INLINE_CODE.finditer(text):
        span = match.group(1).strip()
        if PREFIXLESS.match(span):
            calls.append(span)
    return calls


def option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return set(parser._option_string_actions)


@pytest.fixture(params=skill_files(), ids=skill_ids())
def skill(request) -> Path:
    return request.param


def test_every_core_skill_has_a_file():
    assert {p.parent.name for p in SKILLS.glob("*/SKILL.md")} == SECTION_6_1 | PLAN_EXTRAS
    assert {p.parent.name for p in (ROOT / "skills" / "gota").glob("*/SKILL.md")} == {
        "replay-inspection",
        "eval-design",
        "diagnosis",
        "ab",
        "survey",
    }


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
        options = option_strings(leaf)
        for word in rest:
            if word.startswith("--"):
                flag = word.split("=", 1)[0]
                assert flag in options, (
                    f"{skill.parent.name}: {' '.join(path)} has no flag {flag} "
                    f"(has {sorted(options)})"
                )


def test_cli_calls_parse_on_the_test_helpers():
    calls = cli_calls(
        'Run `"$AUTORESEARCH_CLI" card finish --outcome done` and\n'
        '"$AUTORESEARCH_CLI" queue request drop Q-1 \\\n  --reason "x" | jq .\n'
        '`"$AUTORESEARCH_CLI" queue request drop <Q-id> --reason "y"` > out.json\n'
        '`"$AUTORESEARCH_CLI" queue request create --title "t"\n   --mode maintenance`\n'
        "Then `stats design <E-id> --request-file F` and see `queue`."
    )
    assert [call.split()[:3] for call in calls] == [
        ["card", "finish", "--outcome"],
        ["queue", "request", "drop"],
        ["queue", "request", "drop"],
        ["queue", "request", "create"],
        ["stats", "design", "<E-id>"],
    ]
    assert "--reason" in calls[1]
    assert "--reason" in calls[2] and "out.json" not in calls[2]  # past the placeholder
    assert "--mode" in calls[3]  # a call wrapped inside one inline-code span
    assert "--request-file" in calls[4]
    with pytest.raises(AssertionError):
        resolve(["queue", "nosuch"])
    _, leaf, _ = resolve(["queue", "request", "drop", "Q-1"])
    assert "--reason" in option_strings(leaf) and "--outcome" not in option_strings(leaf)
    assert BARE_CALL.search("run autoresearch records new")
    assert not BARE_CALL.search('"$AUTORESEARCH_CLI" records new')


def test_no_bare_autoresearch_call(skill):
    for number, line in enumerate(skill.read_text(encoding="utf-8").splitlines(), start=1):
        assert not BARE_CALL.search(line), f"{skill.parent.name}:{number}: {line}"


def test_core_skills_never_name_a_game(skill):
    if skill.parent.parent != SKILLS:
        return
    assert not re.search(
        r"gota|gods of the arena", skill.read_text(encoding="utf-8"), re.IGNORECASE
    )


def test_cited_doctrine_ids_exist(skill):
    cited = set(re.findall(r"\bD-\d{2}\b", skill.read_text(encoding="utf-8")))
    assert cited, f"{skill.parent.name} cites no doctrine rule"
    assert cited <= rule_ids(), cited - rule_ids()


GAME_CALL = re.compile(r'"\$AR_PYTHON" "\$AR_HOME/games/gota/tools/([^"/]+)"([^`\n]*)')


def check_game_call(script: str, arguments: str) -> None:
    # Only advertised read-only instruments may run, and only their --help path.
    assert script in {"replay_stats.py", "evaluate.py", "compare.py"}, script
    path = ROOT / "games" / "gota" / "tools" / script
    assert path.is_file(), path
    result = subprocess.run(
        [sys.executable, str(path), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    supported = set(re.findall(r"--[a-z][a-z0-9-]*", result.stdout))
    for flag in re.findall(r"--[a-z][a-z0-9-]*", arguments):
        assert flag in supported, f"{script}: unknown flag {flag}"


def test_game_tool_commands_and_flags_exist(skill):
    if skill.parent.parent == SKILLS:
        return
    calls = GAME_CALL.findall(skill.read_text(encoding="utf-8"))
    assert calls, f"{skill}: no GotA instrument command"
    for script, arguments in calls:
        check_game_call(script, arguments)


def test_game_command_regressions_fail_closed():
    with pytest.raises(AssertionError):
        check_game_call("missing.py", "--json")
    with pytest.raises(AssertionError, match="unknown flag"):
        check_game_call("evaluate.py", "--invented-option")
    with pytest.raises(AssertionError):
        resolve(["softmax", "missing-command"])
