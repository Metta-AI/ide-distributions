from pathlib import Path

import pytest

from research_core import gitops


@pytest.fixture
def repo(tmp_path) -> Path:
    path = gitops.init_repo(tmp_path / "repo")
    (path / "file.txt").write_text("".join(f"line {n}\n" for n in range(1, 11)))
    gitops.commit_all(path, "Add ten lines")
    return path


def test_init_commit_rev_parse_and_cleanliness(repo):
    assert gitops.current_branch(repo) == "main"
    head = gitops.rev_parse(repo, "HEAD")
    assert len(head) == 40 and gitops.rev_parse(repo, "main") == head
    assert gitops.is_clean(repo) and gitops.dirty_files(repo) == []
    entries = gitops.log(repo, "HEAD", 10)
    assert [e["subject"] for e in entries] == ["Add ten lines", "Initialize repository"]
    assert entries[0]["sha"] == head and entries[0]["author"] == "IDE"

    (repo / "file.txt").write_text("changed\n")
    (repo / "new.txt").write_text("new\n")
    assert gitops.dirty_files(repo) == ["file.txt", "new.txt"]
    assert not gitops.is_clean(repo)
    sha = gitops.commit_all(repo, "Change things", author="Tester <t@example.invalid>")
    assert gitops.log(repo, "HEAD", 1)[0] == {
        "sha": sha,
        "author": "Tester",
        "date": gitops.log(repo, "HEAD", 1)[0]["date"],
        "subject": "Change things",
    }
    # init_repo is idempotent: no second initial commit.
    gitops.init_repo(repo)
    assert len(gitops.log(repo, "HEAD", 10)) == 3


def test_git_error_names_command_exit_code_and_stderr(repo):
    with pytest.raises(gitops.GitError) as info:
        gitops.git(repo, "rev-parse", "--verify", "no-such-ref")
    message = str(info.value)
    assert "git -C" in message and "rev-parse --verify no-such-ref" in message
    assert "exit code 128" in message and "fatal" in message
    assert info.value.exit_code == 128 and info.value.command[-1] == "no-such-ref"
    # Nothing to commit is an error with git's own explanation.
    with pytest.raises(gitops.GitError, match="nothing to commit"):
        gitops.commit_all(repo, "Empty")
    assert gitops.commit_all(repo, "Empty", allow_empty=True)


def test_worktree_add_reuse_and_remove(repo, tmp_path):
    path = tmp_path / "worktrees" / "task-1"
    assert gitops.worktree_add(repo, path, branch="task/1") == path
    assert gitops.current_branch(path) == "task/1"
    assert gitops.rev_parse(path, "HEAD") == gitops.rev_parse(repo, "main")
    # Reused when already at that branch.
    assert gitops.worktree_add(repo, path, branch="task/1") == path
    listed = {w["branch"]: w for w in gitops.list_worktrees(repo)}
    assert set(listed) == {"main", "task/1"}
    assert listed["task/1"]["path"].resolve() == path.resolve()
    with pytest.raises(gitops.GitError, match="is a worktree of task/1, not task/2"):
        gitops.worktree_add(repo, path, branch="task/2")

    gitops.worktree_remove(repo, path)
    assert not path.exists()
    assert [w["branch"] for w in gitops.list_worktrees(repo)] == ["main"]
    # The branch survives; adding it again checks out the existing branch.
    gitops.worktree_add(repo, path, branch="task/1")
    assert gitops.current_branch(path) == "task/1"
    (path / "scratch.txt").write_text("x\n")
    with pytest.raises(gitops.GitError):
        gitops.worktree_remove(repo, path)
    gitops.worktree_remove(repo, path, force=True)
    assert not path.exists()


def test_merge_no_ff_happy_path(repo, tmp_path):
    worktree = gitops.worktree_add(repo, tmp_path / "wt" / "task-1", branch="task/1")
    (worktree / "policy.txt").write_text("patience = 3\n")
    task_commit = gitops.commit_all(worktree, "Raise patience")
    main_before = gitops.rev_parse(repo, "main")
    assert gitops.commit_on_branch(repo, task_commit, "task/1")
    assert not gitops.commit_on_branch(repo, task_commit, "main")
    assert not gitops.commit_on_branch(repo, "0" * 40, "task/1")
    assert not gitops.is_ancestor(repo, task_commit, "main")

    merged = gitops.merge_no_ff(repo, task_commit, message="Merge T-1")
    assert gitops.rev_parse(repo, "main") == merged != main_before
    parents = gitops.git(repo, "rev-list", "--parents", "-n1", merged).stdout.split()[1:]
    assert parents == [main_before, task_commit]
    assert gitops.is_ancestor(repo, task_commit, "main")
    assert gitops.is_ancestor(repo, main_before, merged)
    # main is checked out in repo: its working tree moved with the branch and stays clean.
    assert (repo / "policy.txt").read_text() == "patience = 3\n"
    assert gitops.is_clean(repo)
    assert gitops.log(repo, "main", 1)[0]["subject"] == "Merge T-1"
    # The temporary merge worktree is gone.
    assert sorted(w["branch"] for w in gitops.list_worktrees(repo)) == ["main", "task/1"]
    # Merging again is a no-op that returns the tip.
    assert gitops.merge_no_ff(repo, task_commit, message="again") == merged

    gitops.tag(repo, "v/1", merged)
    assert gitops.rev_parse(repo, "v/1") == merged
    gitops.tag(repo, "v/1", merged)  # same target: left alone
    with pytest.raises(gitops.GitError):
        gitops.tag(repo, "v/1", main_before)


def test_merge_conflict_raises_and_leaves_main_untouched(repo, tmp_path):
    worktree = gitops.worktree_add(repo, tmp_path / "wt" / "task-2", branch="task/2")
    (worktree / "file.txt").write_text("task version\n")
    task_commit = gitops.commit_all(worktree, "Task edit")
    (repo / "file.txt").write_text("main version\n")
    main_tip = gitops.commit_all(repo, "Main edit")

    with pytest.raises(gitops.MergeConflict) as info:
        gitops.merge_no_ff(repo, task_commit, message="Merge T-2")
    assert info.value.files == ["file.txt"]
    assert info.value.commit == task_commit and info.value.into == "main"
    assert "file.txt" in str(info.value)
    assert gitops.rev_parse(repo, "main") == main_tip
    assert gitops.is_clean(repo)
    assert (repo / "file.txt").read_text() == "main version\n"
    assert sorted(w["branch"] for w in gitops.list_worktrees(repo)) == ["main", "task/2"]
    assert not gitops.is_ancestor(repo, task_commit, "main")


def test_merge_into_branch_not_checked_out_moves_the_ref(repo, tmp_path):
    gitops.git(repo, "branch", "release", "main")
    worktree = gitops.worktree_add(
        repo, tmp_path / "wt" / "task-3", branch="task/3", base="release"
    )
    (worktree / "extra.txt").write_text("extra\n")
    task_commit = gitops.commit_all(worktree, "Extra")
    merged = gitops.merge_no_ff(repo, task_commit, message="Merge into release", into="release")
    assert gitops.rev_parse(repo, "release") == merged
    assert gitops.rev_parse(repo, "main") != merged
    assert not (repo / "extra.txt").exists()


def test_show_lines_and_lines_changed_since(repo):
    sha = gitops.rev_parse(repo, "HEAD")
    assert gitops.show_lines(repo, "file.txt", 2, 4, sha) == ["line 2", "line 3", "line 4"]
    assert gitops.show_lines(repo, "file.txt", 9, 20, sha) == ["line 9", "line 10"]
    with pytest.raises(ValueError):
        gitops.show_lines(repo, "file.txt", 3, 2, sha)
    with pytest.raises(gitops.GitError):
        gitops.show_lines(repo, "missing.txt", 1, 1, sha)

    assert not gitops.lines_changed_since(repo, "file.txt", 1, 10, sha)
    # Change line 8 only.
    lines = (repo / "file.txt").read_text().splitlines()
    lines[7] = "line 8 changed"
    (repo / "file.txt").write_text("".join(f"{line}\n" for line in lines))
    gitops.commit_all(repo, "Change line 8")
    assert not gitops.lines_changed_since(repo, "file.txt", 1, 3, sha)
    assert gitops.lines_changed_since(repo, "file.txt", 7, 9, sha)
    assert gitops.lines_changed_since(repo, "file.txt", 8, 8, sha)
    assert not gitops.lines_changed_since(repo, "file.txt", 9, 10, sha)
    # Insert a line between old lines 2 and 3: it changes a range only when it lands inside.
    lines.insert(2, "inserted")
    (repo / "file.txt").write_text("".join(f"{line}\n" for line in lines))
    gitops.commit_all(repo, "Insert after line 2")
    assert gitops.lines_changed_since(repo, "file.txt", 1, 5, sha)
    assert gitops.lines_changed_since(repo, "file.txt", 2, 3, sha)
    assert not gitops.lines_changed_since(repo, "file.txt", 3, 6, sha)
    assert not gitops.lines_changed_since(repo, "file.txt", 1, 2, sha)
    # Deleted at HEAD, or absent at the cited commit: changed.
    (repo / "file.txt").unlink()
    gitops.commit_all(repo, "Delete the file")
    assert gitops.lines_changed_since(repo, "file.txt", 1, 1, sha)
    assert gitops.lines_changed_since(repo, "never.txt", 1, 1, sha)


def test_clone_pinned_from_a_bare_repo_at_an_older_commit(repo, tmp_path):
    first = gitops.rev_parse(repo, "HEAD")
    (repo / "later.txt").write_text("later\n")
    second = gitops.commit_all(repo, "Later commit")
    bare = tmp_path / "upstream.git"
    gitops.git(None, "clone", "--quiet", "--bare", str(repo), str(bare))
    url = bare.resolve().as_uri()

    dest = tmp_path / "source"
    assert gitops.clone_pinned(url, dest, first) == first
    assert gitops.rev_parse(dest, "HEAD") == first
    assert gitops.current_branch(dest) is None
    assert (dest / "file.txt").exists() and not (dest / "later.txt").exists()
    # Reused on a second call for another commit.
    assert gitops.clone_pinned(url, dest, second) == second
    assert (dest / "later.txt").exists()
