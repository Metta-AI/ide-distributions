"""The profile works under any install name (plan Task 27, D2).

Softmax hosting installs the distribution as ``optimizer``; a manual install uses
``autoresearch``. The worker profile (the card assignee, the name preflight looks for in
``dispatch_profiles``) is the profile home's name; the Kanban board is always
``autoresearch``, whatever the install name.
"""

import datetime
import re
from pathlib import Path

import pytest
import yaml
from support import keeper_host

from ide_research_core.plugin import peers
from ide_research_core.queue.select import queue_from_env
from ide_research_core.runctx import BOARD, RunContext
from ide_research_core.runtime.preflight import host_config_problems

DISTRIBUTION = Path(__file__).resolve().parents[2]
INSTALL_NAME = "optimizer"


def optimizer_ctx(tmp_path: Path) -> RunContext:
    home = tmp_path / "root" / "profiles" / INSTALL_NAME
    return RunContext(home, home / "workspace", None, None, None)


def test_profile_is_the_profile_home_name(tmp_path):
    assert optimizer_ctx(tmp_path).profile == INSTALL_NAME


def test_board_slug_is_autoresearch_whatever_the_install_name(tmp_path):
    assert BOARD == "autoresearch"
    queue = queue_from_env({}, optimizer_ctx(tmp_path))
    assert (queue.board, queue.profile) == ("autoresearch", INSTALL_NAME)


def test_peers_read_the_autoresearch_board(tmp_path, monkeypatch):
    seen = {}

    class RecordingQueue:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def list(self, status):
            return []

    monkeypatch.setattr(peers, "KanbanQueue", RecordingQueue)
    peers.running_peers_section(optimizer_ctx(tmp_path))
    assert (seen["board"], seen["profile"]) == ("autoresearch", INSTALL_NAME)


def test_preflight_looks_for_the_install_name_in_dispatch_profiles(tmp_path):
    ctx = optimizer_ctx(tmp_path)
    config = ctx.hermes_home.parent.parent / "config.yaml"
    config.parent.mkdir(parents=True)

    config.write_text(yaml.safe_dump({"kanban": {"dispatch_profiles": ["autoresearch"]}}))
    reasons, _ = host_config_problems(ctx.hermes_home)
    assert len(reasons) == 1 and "'optimizer'" in reasons[0]

    config.write_text(yaml.safe_dump({"kanban": {"dispatch_profiles": [INSTALL_NAME]}}))
    assert host_config_problems(ctx.hermes_home) == ([], [])


def test_admission_assigns_cards_to_the_install_name(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper_host, "PROFILE", INSTALL_NAME)
    host = keeper_host.Host(tmp_path)
    assert host.ctx.profile == INSTALL_NAME
    now = datetime.datetime.now(datetime.UTC)
    host.tick(now)  # requests the scheduled planning card
    host.tick(now + datetime.timedelta(minutes=1))  # admits it
    [card] = host.queue.list()
    assert card.spec.mode == "planning" and card.assignee == INSTALL_NAME


# -- docs: the agent's text never hard-codes the install name ----------------------------

HARD_CODED = re.compile(r"""(-p|--profile)[ =]+["']?autoresearch\b|profiles/autoresearch\b""")
AGENT_TEXT = (
    "skills", "doctrine", "games", "scripts", "plugins",
    "SOUL.md", "config.yaml", "distribution.yaml", "cron/jobs.json",
)  # fmt: skip
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".json"}
README_MANUAL_INSTALL = "## Install the profile"


def agent_text_files() -> list[Path]:
    files = []
    for name in AGENT_TEXT:
        path = DISTRIBUTION / name
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.suffix in TEXT_SUFFIXES)
        else:
            files.append(path)
    return files


@pytest.mark.parametrize("path", agent_text_files(), ids=lambda p: str(p.relative_to(DISTRIBUTION)))
def test_agent_text_does_not_name_the_profile(path):
    hits = [line for line in path.read_text(encoding="utf-8").splitlines() if HARD_CODED.search(line)]
    assert hits == [], f"write `hermes -p <profile>` instead: {hits}"


def readme_sections() -> dict[str, str]:
    readme = (DISTRIBUTION / "README.md").read_text(encoding="utf-8")
    parts = re.split(r"^(## .*)$", readme, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_readme_names_the_profile_only_in_the_manual_install_section():
    sections = readme_sections()
    assert README_MANUAL_INSTALL in sections
    hits = {
        heading: [line for line in body.splitlines() if HARD_CODED.search(line)]
        for heading, body in sections.items()
        if heading != README_MANUAL_INSTALL
    }
    assert {heading: lines for heading, lines in hits.items() if lines} == {}

