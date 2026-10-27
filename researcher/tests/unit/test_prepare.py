"""Retained doctrine upgrades use real Git and preserve conflicting work."""

import shutil
import subprocess
from pathlib import Path

import pytest
from ide_research_core import workspace as workspaces
from ide_research_core.prepare import reconcile_workspace

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("edits", ["none", "dirty", "committed", "unresolved"])
def test_same_version_upgrade_preserves_work(tmp_path, edits):
    source = tmp_path / "source"
    shutil.copytree(ROOT / "doctrine", source / "doctrine")
    profile = tmp_path / "profile"
    (profile / "local").mkdir(parents=True)
    clone, remote = profile / "workspace", tmp_path / "canonical.git"
    reconcile_workspace(profile, clone, remote, source)
    original = (clone / "AGENTS.md").read_text()
    git = ["git", "-C", str(clone)]
    (clone / "research.txt").write_text("retained research")
    subprocess.run([*git, "add", "research.txt"], check=True)
    subprocess.run([*git, "commit", "-qm", "Unpublished research"], check=True)
    (clone / "notes.txt").write_text("uncommitted notes")
    if edits in {"dirty", "committed", "unresolved"}:
        (clone / "AGENTS.md").write_text("local doctrine\n")
    if edits in {"committed", "unresolved"}:
        subprocess.run([*git, "add", "AGENTS.md"], check=True)
        subprocess.run([*git, "commit", "-qm", "Local doctrine"], check=True)
    head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    doctrine = source / "doctrine/research-agent.md"
    doctrine.write_text(
        doctrine.read_text().replace("# Research agent doctrine", "# Updated research doctrine")
    )
    if edits == "unresolved":
        # Publish separately, then leave a real merge conflict for preparation to preserve.
        workspaces.bootstrap(tmp_path / "publisher", remote, None, source)
        subprocess.run([*git, "fetch", "origin", "main"], check=True)
        result = subprocess.run(
            [*git, "merge", "--no-edit", "origin/main"], capture_output=True, check=False
        )
        assert result.returncode != 0
    before = (clone / "AGENTS.md").read_bytes()
    reconcile_workspace(profile, clone, remote, source)
    warning = profile / "local/doctrine-migration-warning.txt"
    assert (clone / "research.txt").read_text() == "retained research"
    assert (clone / "notes.txt").read_text() == "uncommitted notes"
    subprocess.run([*git, "merge-base", "--is-ancestor", head, "HEAD"], check=True)
    if edits == "none":
        assert (clone / "AGENTS.md").read_text() == workspaces.agents_md(source)
        assert (clone / "AGENTS.md").read_text().splitlines()[0] == original.splitlines()[0]
        assert not warning.exists()
        after = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True)
        reconcile_workspace(profile, clone, remote, source)
        assert subprocess.check_output([*git, "rev-parse", "HEAD"], text=True) == after
    else:
        assert (clone / "AGENTS.md").read_bytes() == before
        assert warning.exists()
        assert (clone / ".git/MERGE_HEAD").exists() == (edits == "unresolved")
        if edits == "unresolved":
            subprocess.run([*git, "merge", "--abort"], check=True)
        subprocess.run([*git, "checkout", "origin/main", "--", "AGENTS.md"], check=True)
        subprocess.run([*git, "commit", "-qm", "Resolve doctrine"], check=True)
        reconcile_workspace(profile, clone, remote, source)
        assert not warning.exists()
