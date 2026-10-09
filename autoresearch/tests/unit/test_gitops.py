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


def test_merge_no_ff_clean(repo):
    gitops.git(repo, "checkout", "-b", "side")
    (repo / "s.txt").write_text("s\n")
    side = gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "main")
    ok, conflicts = gitops.merge_no_ff(repo, side, "merge side")
    assert ok and conflicts == []
    assert len(gitops.git(repo, "rev-list", "--parents", "-n1", "HEAD").split()) == 3


def test_merge_no_ff_conflict_aborts_cleanly(repo):
    gitops.git(repo, "checkout", "-b", "side")
    (repo / "f.txt").write_text("side\n")
    side = gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "main")
    (repo / "f.txt").write_text("main\n")
    gitops.commit_all(repo, "main edit")
    before = gitops.head(repo)
    assert gitops.merge_no_ff(repo, side, "m") == (False, ["f.txt"])
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


def test_merge_no_ff_non_conflict_failure_raises_with_stderr(repo):
    with pytest.raises(gitops.GitError) as excinfo:
        gitops.merge_no_ff(repo, "deadbeef" * 5, "m")
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


def test_commit_blobs_ignores_and_keeps_unrelated_staged_changes(repo):
    (repo / "stray.txt").write_text("staged by someone else\n")
    gitops.git(repo, "add", "stray.txt")
    (repo / "inbox.json").write_text("rewritten later\n")  # the committed bytes are the blob's
    (repo / "seen.json").write_text("{}\n")

    sha = gitops.commit_blobs(repo, {"inbox.json": b"validated\n"}, ["seen.json"], "adopt")

    assert gitops.head(repo) == sha
    assert gitops.show(repo, "HEAD", "inbox.json") == b"validated\n"
    assert gitops.show(repo, "HEAD", "seen.json") == b"{}\n"
    assert gitops.show(repo, "HEAD", "stray.txt") is None
    assert gitops.git(repo, "diff", "--cached", "--name-only") == "stray.txt"


def test_commit_blobs_leaves_nothing_staged_when_it_fails(repo):
    before = gitops.head(repo)
    with pytest.raises(gitops.GitError):
        gitops.commit_blobs(repo, {"inbox.json": b"x\n"}, ["missing.json"], "adopt")
    assert gitops.head(repo) == before
    assert gitops.git(repo, "diff", "--cached", "--name-only") == ""
