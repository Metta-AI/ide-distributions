"""Thin subprocess wrappers around git. Every function takes the repo path first."""

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, args: tuple[str, ...], stderr: str):
        super().__init__(f"git {' '.join(args)} failed: {stderr.strip()}")
        self.stderr = stderr


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and result.returncode != 0:
        raise GitError(args, result.stderr.decode(errors="replace"))
    return result


def git(repo: Path, *args: str) -> str:
    """Run git and return stripped stdout; raise GitError on failure."""
    return _run(repo, *args).stdout.decode().strip()


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def commit_all(repo: Path, message: str, allow_empty: bool = False) -> str:
    git(repo, "add", "-A")
    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    git(repo, *args)
    return head(repo)


def commit_paths(repo: Path, paths: list[str], message: str) -> str | None:
    """Commit only ``paths`` (additions, changes and deletions), leaving anything else staged
    or modified untouched. Return the new HEAD, or None if those paths had no changes."""
    git(repo, "add", "-A", "--", *paths)
    if _run(repo, "diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
        return None
    git(repo, "commit", "-m", message, "--", *paths)
    return head(repo)


def diff_paths(repo: Path, a: str, b: str) -> list[str]:
    return sorted({path for _, path in diff_status(repo, a, b)})


def diff_status(repo: Path, a: str, b: str) -> list[tuple[str, str]]:
    """(status letter, path) for each change from a to b; renames appear as D + A."""
    output = git(repo, "diff", "--name-status", "--no-renames", "-z", a, b)
    fields = [field for field in output.split("\0") if field]
    return list(zip(fields[0::2], fields[1::2], strict=True))


def show(repo: Path, sha: str, path: str) -> bytes | None:
    result = _run(repo, "show", f"{sha}:{path}", check=False)
    return result.stdout if result.returncode == 0 else None


def merge_no_ff(repo: Path, sha: str, message: str) -> tuple[bool, list[str]]:
    result = _run(repo, "merge", "--no-ff", "-m", message, sha, check=False)
    if result.returncode == 0:
        return True, []
    if _run(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode != 0:
        # No merge in progress: this was not a conflict (bad sha, dirty tree, ...).
        raise GitError(("merge", sha), result.stderr.decode(errors="replace"))
    conflicts = sorted(git(repo, "diff", "--name-only", "--diff-filter=U").splitlines())
    git(repo, "merge", "--abort")
    return False, conflicts


def contains(repo: Path, sha: str) -> bool:
    """True if sha is an ancestor of (or equal to) HEAD."""
    result = _run(repo, "merge-base", "--is-ancestor", sha, "HEAD", check=False)
    if result.returncode not in (0, 1):
        raise GitError(("merge-base", "--is-ancestor", sha), result.stderr.decode(errors="replace"))
    return result.returncode == 0


def push(repo: Path, remote: str, refspecs: list[str]) -> bool:
    return _run(repo, "push", remote, *refspecs, check=False).returncode == 0


def merge_base(repo: Path, a: str, b: str) -> str:
    return git(repo, "merge-base", a, b)
