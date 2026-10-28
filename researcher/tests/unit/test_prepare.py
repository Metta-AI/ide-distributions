"""Prepare publishes current doctrine to every game repository on the host.

Real git repositories; a stub stands in for the Hermes CLI, whose settings this test ignores."""

import subprocess
from pathlib import Path

from ide_research_core import prepare, workspace
from ide_research_core.io import gitops

DISTRIBUTION = Path(__file__).resolve().parents[2]


def main_of(bare: Path) -> str:
    return gitops.git(bare, "rev-parse", "main")


def agents_on_main(bare: Path) -> str:
    return subprocess.check_output(["git", "--git-dir", str(bare), "show", "main:AGENTS.md"], text=True)


def test_prepare_refreshes_doctrine_in_existing_game_repos_without_touching_clones(
    tmp_path, monkeypatch
):
    home = tmp_path / "host"
    profile = home / "profiles" / "researcher-1"
    profile.mkdir(parents=True)
    neutral = home / "canonical.git"
    game = home / "canonical-fakegame.git"
    expected = workspace.agents_md(DISTRIBUTION)

    # A game repository last seeded by an older doctrine.
    seeder = tmp_path / "seeder"
    workspace.bootstrap(seeder, game, "fakegame", DISTRIBUTION)
    workspace.publish(seeder, {"AGENTS.md": b"<!-- doctrine-version: 1 -->\nold\n"}, "Old doctrine")
    # This profile's retained clone of that game, busy with uncommitted work.
    retained = profile / "workspace-fakegame"
    gitops.git(tmp_path, "clone", "-q", str(game), str(retained))
    (retained / "plan.md").write_text("half-written plan\n")
    (retained / "notes.txt").write_text("untracked notes\n")
    retained_head = gitops.head(retained)
    retained_status = gitops.git(retained, "status", "--porcelain")

    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\nexit 0\n")
    hermes.chmod(0o755)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HERMES_REAL_HOME", str(home))
    monkeypatch.setenv("RESEARCH_WORKSPACE", str(profile / "workspace"))
    monkeypatch.setenv("RESEARCH_CANONICAL", str(neutral))

    prepare.prepare(DISTRIBUTION, str(hermes), activate_supervisor=False)

    assert agents_on_main(game) == expected
    assert agents_on_main(neutral) == expected
    # The retained clone is exactly as the worker left it ...
    assert gitops.head(retained) == retained_head
    assert gitops.git(retained, "status", "--porcelain") == retained_status
    assert (retained / "AGENTS.md").read_text().endswith("old\n")
    assert (retained / "plan.md").read_text() == "half-written plan\n"
    # ... and its next card, branched from fetched origin/main, has the new doctrine.
    gitops.git(retained, "fetch", "-q", "origin")
    card = subprocess.check_output(
        ["git", "-C", str(retained), "show", "origin/main:AGENTS.md"], text=True
    )
    assert card == expected

    heads = (main_of(game), main_of(neutral))
    prepare.prepare(DISTRIBUTION, str(hermes), activate_supervisor=False)
    assert (main_of(game), main_of(neutral)) == heads
