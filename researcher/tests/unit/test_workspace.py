"""The canonical workspace: bootstrap, integrate, merged and publish against real git repos.

One bare repository stands for ``<root>/canonical-<game>.git``; each test builds the clones
of two researchers and one orchestrator from it, the way three profiles on one host would.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ide_research_core import workspace
from ide_research_core.io import gitops
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.workspace import WorkspaceError

from records.fixtures import CLAIM, RESULT

DISTRIBUTION = Path(__file__).resolve().parents[2]


@pytest.fixture
def bare(tmp_path) -> Path:
    return tmp_path / "canonical-fakegame.git"


def clone_for(tmp_path: Path, bare: Path, name: str) -> Path:
    clone = tmp_path / name / "workspace"
    workspace.bootstrap(clone, bare, "fakegame", DISTRIBUTION)
    return clone


def card_worktree(clone: Path, card: str) -> Path:
    """What the Kanban dispatcher does for a worktree card: a worktree of the clone on the
    branch ``card_create`` made at the fast-forwarded ``main``."""
    path = clone / ".worktrees" / card
    gitops.git(clone, "worktree", "add", "-q", "-b", f"card/{card}", str(path), "HEAD")
    return path


def store_of(root: Path) -> RecordStore:
    return RecordStore(Layout(root))


def test_first_bootstrap_seeds_and_later_ones_clone(tmp_path, bare):
    first = clone_for(tmp_path, bare, "researcher-1")
    second = clone_for(tmp_path, bare, "researcher-2")
    assert (first / "strategy" / "STRATEGY.md").is_file()
    assert (first / "AGENTS.md").read_text().startswith("<!-- doctrine-version: ")
    assert gitops.head(first) == gitops.head(second)
    assert workspace.bootstrap(second, bare, "fakegame", DISTRIBUTION) == []


def test_integrate_merges_a_card_and_knows_it_is_merged(tmp_path, bare):
    clone = clone_for(tmp_path, bare, "researcher-1")
    worktree = card_worktree(clone, "a")
    claim = store_of(worktree).create("claim", CLAIM, "Damage is 10.", slug="damage")

    done = workspace.integrate(clone, worktree=worktree)

    assert not done.already_merged
    assert workspace.merged(clone, "card/a") == {
        "merged": True, "card_sha": done.card_sha, "main_sha": done.main_sha,
    }
    other = clone_for(tmp_path, bare, "researcher-2")
    gitops.git(other, "pull", "-q")
    assert store_of(other).load(claim)[1] == "Damage is 10."
    assert (other / "kb" / "index.md").is_file()  # views regenerated after the merge
    assert workspace.integrate(clone, worktree=worktree).already_merged


def test_the_orchestrator_integrates_a_pushed_branch(tmp_path, bare):
    researcher = clone_for(tmp_path, bare, "researcher-1")
    worktree = card_worktree(researcher, "b")
    store_of(worktree).create("claim", CLAIM, "Armor halves damage.", slug="armor")
    gitops.git(worktree, "add", "-A")
    gitops.git(worktree, "commit", "-q", "-m", "result")
    gitops.git(worktree, "push", "-q", "origin", "card/b")
    orchestrator = clone_for(tmp_path, bare, "orchestrator")

    assert not workspace.merged(orchestrator, "card/b")["merged"]
    done = workspace.integrate(orchestrator, branch="card/b")
    assert workspace.merged(researcher, "card/b")["merged"]
    assert done.main_sha == gitops.git(bare, "rev-parse", "main")


def test_validation_refuses_an_immutable_change_after_pushing_the_branch(tmp_path, bare):
    clone = clone_for(tmp_path, bare, "researcher-1")
    first = card_worktree(clone, "c")
    result = store_of(first).create("result", RESULT, "analysis A")
    workspace.integrate(clone, worktree=first)
    second = card_worktree(clone, "d")
    path = store_of(second).path_of(result)
    path.write_text(path.read_text().replace("analysis A", "analysis B"))

    with pytest.raises(WorkspaceError) as refused:
        workspace.integrate(clone, worktree=second)
    assert refused.value.reason == "validate"
    assert gitops.git(bare, "rev-parse", "card/d")  # recovery is branch-based


def test_a_card_may_not_change_the_champion_or_control(tmp_path, bare):
    clone = clone_for(tmp_path, bare, "researcher-1")
    worktree = card_worktree(clone, "e")
    (worktree / "strategy" / "STRATEGY.md").write_text("a new champion\n")
    with pytest.raises(WorkspaceError, match="only the orchestrator") as refused:
        workspace.integrate(clone, worktree=worktree)
    assert refused.value.reason == "validate"


def test_two_researchers_changing_one_record_overlap(tmp_path, bare):
    one = clone_for(tmp_path, bare, "researcher-1")
    seed_card = card_worktree(one, "f")
    claim = store_of(seed_card).create("claim", CLAIM, "Damage is 10.", slug="damage")
    workspace.integrate(one, worktree=seed_card)
    two = clone_for(tmp_path, bare, "researcher-2")
    first, second = card_worktree(one, "g"), card_worktree(two, "h")
    store_of(first).update(claim, {}, body="Damage is 10, armor halves it.")
    store_of(second).update(claim, {}, body="Damage is 12.")

    workspace.integrate(one, worktree=first)
    with pytest.raises(WorkspaceError) as refused:
        workspace.integrate(two, worktree=second)
    assert refused.value.reason == "overlap"
    assert refused.value.details["overlap"] == [claim]


def test_a_text_conflict_is_a_merge_failure(tmp_path, bare):
    one = clone_for(tmp_path, bare, "researcher-1")
    two = clone_for(tmp_path, bare, "researcher-2")
    first, second = card_worktree(one, "i"), card_worktree(two, "j")
    (first / "notes.md").write_text("one\n")
    (second / "notes.md").write_text("two\n")

    workspace.integrate(one, worktree=first)
    with pytest.raises(WorkspaceError) as refused:
        workspace.integrate(two, worktree=second)
    assert refused.value.reason == "merge"
    assert refused.value.details["conflicts"] == ["notes.md"]
    assert not (two / ".git" / "MERGE_HEAD").exists()


def test_publish_commits_only_the_named_paths(tmp_path, bare):
    orchestrator = clone_for(tmp_path, bare, "orchestrator")
    (orchestrator / "plan.md").write_text("# Plan\n\nNext: armor.\n")
    (orchestrator / "control").mkdir()
    (orchestrator / "control" / "pause.json").write_text('{"paused": true}\n')
    (orchestrator / "scratch.txt").write_text("not mine to publish\n")

    published = workspace.publish(orchestrator, ["plan.md", "control/pause.json"], "plan")

    assert published["main_sha"] == gitops.git(bare, "rev-parse", "main")
    tracked = gitops.git(bare, "ls-tree", "-r", "--name-only", "main").splitlines()
    assert "control/pause.json" in tracked and "scratch.txt" not in tracked


def test_the_cli_reports_a_refusal_as_json(tmp_path, bare):
    clone = clone_for(tmp_path, bare, "researcher-1")
    worktree = card_worktree(clone, "k")
    (worktree / "control").mkdir()
    (worktree / "control" / "pause.json").write_text("{}\n")
    run = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "workspace", "integrate", "--root", str(clone)],
        cwd=worktree, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(DISTRIBUTION / "src")},
    )
    assert run.returncode == 1
    assert '"reason": "validate"' in run.stdout
