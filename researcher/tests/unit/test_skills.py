"""File-only researcher prompt contracts, independent of the shared CLI implementation.

The command/flag contract comes from Round 10 and the unchanged research commands.
Game instrument flags are read from source, never executed. These tests validate
prompt interfaces and role boundaries, not runtime integration or platform behavior.
"""

import ast
import json
import re
from pathlib import Path

import frontmatter
import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills" / "autoresearch"
RULE_LINE = re.compile(r"^(D-\d{2})\. ", re.MULTILINE)

# Round 10 replaces lifecycle, communication and submission skills.
CORE_SKILLS = {
    "card",
    "report",
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
    "candidate",
    "lessons",
}
# Plan 03 Task 4.2 adds these for System Design §5.6 and C10.
MAINTENANCE_SKILLS = {"kb-maintenance", "calibrate-noise"}

NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")  # Hermes skill_manager_tool.VALID_NAME_RE
DESCRIPTION_LIMIT = 60  # Hermes SKILL_PROMPT_DESC_LIMIT: longer descriptions are truncated
# A CLI call: the quoted variable, then words up to the end of the line, an inline-code
# backtick, or a shell operator. Backslash-newline continues a line. ``>`` ends a call only
# after a space (a redirect), so placeholders such as ``<E-id>`` stay inside the call.
CLI_CALL = re.compile(r'"\$RESEARCH_CLI"((?:\\\n|[^`\n|;&>)]|(?<! )>)*)')
# A single-backtick inline-code span (not a fence); it may wrap across lines.
INLINE_CODE = re.compile(r"(?<!`)`(?!`)([^`]+?)`(?!`)")
GROUPS = (
    "records",
    "stats",
    "kb",
    "card",
    "comms",
    "queue",
    "promotion",
    "softmax",
    "game",
    "workspace",
    "runtime",
    "provenance",
    "report",
)
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


# Explicit contract keeps these tests usable while shared/ changes independently.
COMMAND_FLAGS = {
    "records new": "--meta --meta-file --body --body-file --slug",
    "records show": "",
    "records update": "--patch --patch-file --body --body-file",
    "records status": "--reason --ref",
    "records link": "",
    "records find": "--where",
    "records validate": "--all",
    "records summary": "",
    "records resolve": "",
    "stats ingest": "--rows --league --experiment --game-version --game",
    "stats noise": "--league --game-version --metrics --evidence",
    "stats design": "--request-file --planned-pairs --game",
    "stats evaluate": "--champion",
    "kb lint": "",
    "kb apply": "",
    "kb stale": "--version",
    "kb renew": "--game-repo --old --new --version",
    "kb compact": "",
    "kb hubs": "--size-limit-tokens",
    "kb index": "--token-budget",
    "game show": "",
    "softmax whoami": "",
    "softmax credits": "",
    "softmax xp create": "--batch --attempt --batches",
    "softmax xp status": "",
    "softmax xp recover": "",
    "softmax xp artifacts": "--dest --batch --attempt",
    "softmax xp evidence": "",
    "softmax upload": "--build-dir --name",
    "softmax upload-build": "--build-dir --name",
    "softmax leaderboard": "",
    "softmax game-version": "",
    "softmax standings": "",
    "softmax league-episodes": "--league --limit --out",
    "workspace stage": "--worktree",
    "workspace merged": "--branch",
}


def check_cli_call(call: str) -> None:
    words = call.split()
    if not words or words == ["<group>", "<command>"]:
        return  # Invocation convention in prose.
    size = 3 if words[:2] == ["softmax", "xp"] else 2
    command = " ".join(words[:size])
    assert command in COMMAND_FLAGS, f"unknown researcher command: {command}"
    options = set(COMMAND_FLAGS[command].split()) | {"--help"}
    if words[0] in {"records", "stats", "kb"}:
        options.add("--root")
    for flag in re.findall(r"--[a-z][a-z0-9-]*", " ".join(words[size:])):
        assert flag in options, f"{command}: unknown flag {flag}"


def _unwrap_inline_code(text: str) -> str:
    """Join inline-code spans that wrap across lines, so a wrapped call reads as one line."""
    return INLINE_CODE.sub(lambda m: "`" + " ".join(m.group(1).split("\n")) + "`", text)


def cli_calls(text: str) -> list[str]:
    """Every CLI call: ``"$RESEARCH_CLI" ...`` ones, then inline-code spans that start
    with a group name (such as ``stats design <E-id>``), as the words after the program."""
    text = _unwrap_inline_code(text)
    calls = [match.group(1).replace("\\\n", " ") for match in CLI_CALL.finditer(text)]
    for match in INLINE_CODE.finditer(text):
        span = match.group(1).strip()
        if PREFIXLESS.match(span):
            calls.append(span)
    return calls


@pytest.fixture(params=skill_files(), ids=skill_ids())
def skill(request) -> Path:
    return request.param


def test_every_core_skill_has_a_file():
    assert {p.parent.name for p in SKILLS.glob("*/SKILL.md")} == CORE_SKILLS | MAINTENANCE_SKILLS
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


def test_cli_commands_and_flags_match_researcher_contract(skill):
    for call in cli_calls(skill.read_text(encoding="utf-8")):
        check_cli_call(call)


def test_command_checks_cover_multiline_calls_and_fail_closed():
    calls = cli_calls(
        '"$RESEARCH_CLI" workspace merged --branch card/42\n'
        '`"$RESEARCH_CLI" records new journal\n'
        " --meta-file F --body-file B`\n"
        "`softmax xp artifacts <E-id> --dest D`"
    )
    assert len(calls) == 3
    for call in calls:
        check_cli_call(call)
    for invalid in (
        "comms stage",
        "queue request create",
        "card finish",
        "promotion submit",
        "softmax submit",
        "softmax bootstrap-champion",
        "runtime status",
        "status",
        "report baseline",
        "provenance check",
        "kb hubs --request",
        "workspace publish",
        "workspace integrate --branch card/other",
        "stats design E-1 --invented-option",
    ):
        with pytest.raises(AssertionError):
            check_cli_call(invalid)


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


GAME_CALL = re.compile(r'"\$RESEARCH_PYTHON" "\$RESEARCH_HOME/games/gota/tools/([^"/]+)"([^`\n]*)')


def check_game_call(script: str, arguments: str) -> None:
    assert script in {
        "replay_stats.py",
        "evaluate.py",
        "compare.py",
        "make_v13_live.py",
        "build.py",
    }, script
    path = ROOT / "games" / "gota" / "tools" / script
    tree = ast.parse(path.read_text(encoding="utf-8"))
    supported = {
        argument.value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_argument"
        for argument in call.args
        if isinstance(argument, ast.Constant)
        and isinstance(argument.value, str)
        and argument.value.startswith("--")
    }
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
        check_cli_call("softmax missing-command")


def test_removed_transport_and_scheduling_are_not_in_prompts(skill):
    text = skill.read_text(encoding="utf-8")
    for obsolete in (
        "AUTORESEARCH_",
        "queue-keeper",
        "xp-poll",
        "outbox",
        "inbox",
        "briefing",
        "cards/requests/",
        "spend cap",
        "credit floor",
        "chat cap",
        "kanban_create",
        "kanban_link",
        "kanban_schedule",
    ):
        assert obsolete not in text, f"{skill.parent.name}: {obsolete}"


def test_native_completion_example_does_not_require_integration_or_journal():
    text = (SKILLS / "card" / "SKILL.md").read_text(encoding="utf-8")
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.DOTALL)]
    assert len(examples) == 1
    assert set(examples[0]) == {"summary", "metadata"}
    assert "artifacts" in examples[0]["metadata"]
    assert '"$RESEARCH_CLI" workspace stage' in text
    assert "workspace integrate" not in text
    assert "kanban_complete" in text and "kanban_block" in text


def test_candidate_handoff_has_the_reviewed_evidence_and_upload_only_bootstrap():
    text = (SKILLS / "candidate" / "SKILL.md").read_text(encoding="utf-8")
    for required in (
        "decision.outcome: promote",
        "stats/outcomes/<E-id>.json",
        "strategy/candidates/<E-id>/STRATEGY.md",
        "uploaded candidate binding",
        "softmax upload-build",
        "version_id",
        "platform_policy_version_id",
        "explicitly assigned a bootstrap card",
    ):
        assert required in text


def _subparsers(parser):
    import argparse

    found = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    return found[0].choices if found else {}


def test_the_contract_matches_the_real_cli():
    """The table above is the researcher's allowed subset; every command and flag in it must
    exist in the shared CLI, so a renamed command fails here instead of in a worker."""
    from ide_research_core.cli import build_parser

    groups = _subparsers(build_parser())
    for command, flags in COMMAND_FLAGS.items():
        parser = groups[command.split()[0]]
        for word in command.split()[1:]:
            parser = _subparsers(parser)[word]
        known = {option for action in parser._actions for option in action.option_strings}
        for flag in flags.split():
            assert flag in known, f"{command}: {flag} is not a flag of the real CLI"


SKILL_REFERENCE = re.compile(
    r"`([a-z][a-z0-9-]+)` skill|skills: \[([^\]]*)\]|[Ss]kill `([a-z][a-z0-9-]+)`"
)


def test_every_skill_named_in_a_soul_or_game_file_exists():
    """No SOUL.md or GAME.md may send a mind to a skill that does not ship (the hosted
    transcript asked for a nonexistent `research` skill)."""
    shipped = set(skill_ids())
    files = [ROOT / "SOUL.md", ROOT.parent / "orchestrator" / "SOUL.md"]
    files += sorted((ROOT / "games").glob("*/GAME.md"))
    named = set()
    for path in files:
        for match in SKILL_REFERENCE.finditer(path.read_text(encoding="utf-8")):
            names = [match.group(1), match.group(3)]
            names += re.findall(r'"([a-z][a-z0-9-]+)"', match.group(2) or "")
            named |= {name for name in names if name}
    assert named <= shipped, named - shipped
