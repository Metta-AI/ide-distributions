import subprocess

import pytest

from autoresearch.io import gitops


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    gitops.git(path, "init", "-b", "main")
    gitops.git(path, "config", "user.email", "t@example.com")
    gitops.git(path, "config", "user.name", "T")
    (path / "f.txt").write_text("base\n")
    (path / "gone.txt").write_text("bye\n")
    gitops.commit_all(path, "base")
    return path


def test_head_and_commit_all_allow_empty(repo):
    first = gitops.head(repo)
    with pytest.raises(gitops.GitError):
        gitops.commit_all(repo, "nothing")
    second = gitops.commit_all(repo, "empty", allow_empty=True)
    assert second != first and gitops.head(repo) == second


def test_diff_status_reports_deletion_and_paths(repo):
    base = gitops.head(repo)
    (repo / "gone.txt").unlink()
    (repo / "new.txt").write_text("n\n")
    (repo / "f.txt").write_text("changed\n")
    tip = gitops.commit_all(repo, "change")
    assert sorted(gitops.diff_status(repo, base, tip)) == [
        ("A", "new.txt"),
        ("D", "gone.txt"),
        ("M", "f.txt"),
    ]
    assert gitops.diff_paths(repo, base, tip) == ["f.txt", "gone.txt", "new.txt"]


def test_show_and_missing(repo):
    sha = gitops.head(repo)
    assert gitops.show(repo, sha, "f.txt") == b"base\n"
    assert gitops.show(repo, sha, "nope.txt") is None


def test_contains_and_merge_base(repo):
    base = gitops.head(repo)
    gitops.git(repo, "checkout", "-b", "side")
    (repo / "s.txt").write_text("s\n")
    side = gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "main")
    assert not gitops.contains(repo, side)
    assert gitops.contains(repo, base)
    assert gitops.merge_base(repo, side, "main") == base


def _land(repo, prepared):
    gitops.land(repo, prepared)
    gitops.settle(repo, prepared)
    return prepared.sha


def test_prepare_merge_builds_the_merge_before_head_moves(repo):
    base = gitops.head(repo)
    gitops.git(repo, "checkout", "-b", "side")
    (repo / "s.txt").write_text("s\n")
    side = gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "main")
    prepared, conflicts = gitops.prepare_merge(repo, side, "merge side")
    assert conflicts == [] and prepared.parent == base and gitops.head(repo) == base
    assert _land(repo, prepared) == gitops.head(repo)
    assert len(gitops.git(repo, "rev-list", "--parents", "-n1", "HEAD").split()) == 3
    assert gitops.git(repo, "status", "--porcelain") == ""
    assert gitops._run(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode


def test_land_refuses_a_moved_head_and_aborts_a_prepared_merge(repo):
    gitops.git(repo, "checkout", "-b", "side")
    (repo / "s.txt").write_text("s\n")
    side = gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "main")
    prepared, _ = gitops.prepare_merge(repo, side, "merge side")
    moved = gitops.git(repo, "commit-tree", f"{prepared.parent}^{{tree}}", "-p", prepared.parent,
                       "-m", "someone else")  # fmt: skip
    gitops.git(repo, "update-ref", "HEAD", moved)
    with pytest.raises(gitops.GitError):
        gitops.land(repo, prepared)
    assert gitops.head(repo) == moved
    assert gitops._run(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode


def test_prepare_merge_conflict_aborts_cleanly(repo):
    gitops.git(repo, "checkout", "-b", "side")
    (repo / "f.txt").write_text("side\n")
    side = gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "main")
    (repo / "f.txt").write_text("main\n")
    gitops.commit_all(repo, "main edit")
    before = gitops.head(repo)
    assert gitops.prepare_merge(repo, side, "m") == (None, ["f.txt"])
    assert gitops.head(repo) == before
    assert gitops.git(repo, "status", "--porcelain") == ""


def test_push_success_and_failure(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    gitops.git(repo, "remote", "add", "origin", str(remote))
    assert gitops.push(repo, "origin", ["main"]) is True
    assert gitops.push(repo, "origin", ["nonexistent-ref"]) is False


def test_push_with_rejections_names_the_refs_the_remote_refused(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    gitops.git(repo, "remote", "add", "origin", str(remote))
    gitops.git(repo, "branch", "card/Q-1")
    refspec = ["refs/heads/card/*:refs/heads/card/*"]
    assert gitops.push_with_rejections(repo, "origin", refspec) == (True, [])
    # The card is retried: its branch is rebuilt from the same base, so origin's copy diverges.
    gitops.git(repo, "checkout", "card/Q-1")
    gitops.commit_all(repo, "first attempt", allow_empty=True)
    gitops.push_with_rejections(repo, "origin", refspec)
    gitops.git(repo, "reset", "--hard", "HEAD~1")
    gitops.commit_all(repo, "second attempt", allow_empty=True)
    refspec = ["refs/heads/card/*:refs/heads/card/*"]
    assert gitops.push_with_rejections(repo, "origin", refspec) == (False, ["refs/heads/card/Q-1"])
    assert gitops.push_with_rejections(repo, "origin", ["+" + refspec[0]]) == (True, [])


def test_prepare_merge_non_conflict_failure_raises_with_stderr(repo):
    with pytest.raises(gitops.GitError) as excinfo:
        gitops.prepare_merge(repo, "deadbeef" * 5, "m")
    assert "deadbeef" in excinfo.value.stderr
    assert gitops.git(repo, "status", "--porcelain") == ""


def test_contains_unknown_sha_raises(repo):
    with pytest.raises(gitops.GitError):
        gitops.contains(repo, "deadbeef" * 5)


def test_commit_paths_commits_only_named_paths(repo):
    (repo / "f.txt").write_text("unrelated edit\n")
    (repo / "ledger.json").write_text("{}\n")
    tip = gitops.commit_paths(repo, ["ledger.json"], "ledger")
    assert tip == gitops.head(repo)
    assert gitops.git(repo, "show", "--name-only", "--format=", "HEAD") == "ledger.json"
    assert gitops.git(repo, "status", "--porcelain") == "M f.txt"
    assert gitops.commit_paths(repo, ["ledger.json"], "again") is None
    (repo / "ledger.json").unlink()
    gitops.commit_paths(repo, ["ledger.json"], "remove")
    assert gitops.show(repo, "HEAD", "ledger.json") is None


def test_prepare_blobs_ignores_and_keeps_unrelated_staged_changes(repo):
    (repo / "stray.txt").write_text("staged by someone else\n")
    gitops.git(repo, "add", "stray.txt")
    (repo / "inbox.json").write_text("rewritten later\n")  # the committed bytes are the blob's
    (repo / "seen.json").write_text("{}\n")

    before = gitops.head(repo)
    prepared = gitops.prepare_blobs(repo, {"inbox.json": b"validated\n"}, ["seen.json"], "adopt")
    assert gitops.head(repo) == before and prepared.parent == before  # nothing moved yet
    sha = _land(repo, prepared)

    assert gitops.head(repo) == sha
    assert gitops.show(repo, "HEAD", "inbox.json") == b"validated\n"
    assert gitops.show(repo, "HEAD", "seen.json") == b"{}\n"
    assert gitops.show(repo, "HEAD", "stray.txt") is None
    assert gitops.git(repo, "diff", "--cached", "--name-only") == "stray.txt"


def test_prepare_blobs_leaves_nothing_staged_when_it_fails(repo):
    before = gitops.head(repo)
    with pytest.raises(gitops.GitError):
        gitops.prepare_blobs(repo, {"inbox.json": b"x\n"}, ["missing.json"], "adopt")
    assert gitops.head(repo) == before
    assert gitops.git(repo, "diff", "--cached", "--name-only") == ""


def test_prepare_paths_commits_only_named_paths_and_none_without_changes(repo):
    (repo / "f.txt").write_text("unrelated edit\n")
    (repo / "ledger.json").write_text("{}\n")
    tip = _land(repo, gitops.prepare_paths(repo, ["ledger.json"], "ledger"))
    assert tip == gitops.head(repo)
    assert gitops.git(repo, "show", "--name-only", "--format=", "HEAD") == "ledger.json"
    assert gitops.git(repo, "status", "--porcelain") == "M f.txt"
    assert gitops.prepare_paths(repo, ["ledger.json"], "again") is None
    (repo / "ledger.json").unlink()
    _land(repo, gitops.prepare_paths(repo, ["ledger.json"], "remove"))
    assert gitops.show(repo, "HEAD", "ledger.json") is None
    assert gitops.git(repo, "status", "--porcelain") == "M f.txt"


def test_prepare_paths_makes_a_root_commit(tmp_path):
    repo = tmp_path / "empty"
    gitops.git(tmp_path, "init", "-q", "-b", "main", str(repo))
    gitops.git(repo, "config", "user.email", "t@example.com")
    gitops.git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("a\n")
    prepared = gitops.prepare_paths(repo, ["a.txt"], "first")
    assert prepared.parent is None
    assert _land(repo, prepared) == gitops.head(repo)
    assert gitops.git(repo, "status", "--porcelain") == ""
