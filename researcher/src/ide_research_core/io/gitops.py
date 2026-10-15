"""Thin subprocess wrappers around git. Every function takes the repo path first."""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, args: tuple[str, ...], stderr: str):
        super().__init__(f"git {' '.join(args)} failed: {stderr.strip()}")
        self.stderr = stderr


def _run(
    repo: Path,
    *args: str,
    check: bool = True,
    input: bytes | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False, input=input, env=env
    )
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


@dataclass(frozen=True)
class PreparedCommit:
    """A commit object that no branch points at yet (``prepare_*``). ``land`` moves HEAD to
    it; ``settle`` then brings the real index in line. Building the commit before moving the
    branch lets a caller record its exact sha first."""

    sha: str
    parent: str | None  # HEAD when it was prepared; None in an empty repository
    message: str
    paths: tuple[str, ...] = ()  # real-index entries ``settle`` sets to the new commit
    merge: bool = False  # built from a ``merge --no-commit`` in progress (``prepare_merge``)


def _head_or_none(repo: Path) -> str | None:
    result = _run(repo, "rev-parse", "-q", "--verify", "HEAD", check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else None


def prepare_blobs(
    repo: Path, blobs: dict[str, bytes], paths: list[str], message: str
) -> PreparedCommit | None:
    """Build, without moving any branch, a commit of ``blobs`` (repo-relative path -> exact
    bytes, as regular files, whatever the working tree holds now) plus the working-tree
    content of ``paths`` (additions, changes and deletions), on top of HEAD. None when that
    changes nothing.

    The commit is built in a temporary index, so whatever the real index holds (a leftover
    staged by a crashed writer) neither blocks it nor rides along, and a failure leaves
    nothing staged. ``settle`` later sets the real index entries of exactly these paths."""
    parent = _head_or_none(repo)
    with tempfile.TemporaryDirectory(prefix="ar-index-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        if parent is not None:
            _run(repo, "read-tree", parent, env=env)
        for path, data in blobs.items():
            blob = _run(repo, "hash-object", "-w", "--stdin", input=data).stdout.decode().strip()
            _run(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        if paths:
            _run(repo, "add", "-A", "--", *paths, env=env)
        tree = _run(repo, "write-tree", env=env).stdout.decode().strip()
    if parent is not None and tree == git(repo, "rev-parse", f"{parent}^{{tree}}"):
        return None
    commit = git(repo, "commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
    return PreparedCommit(commit, parent, message, (*blobs, *paths))


def prepare_paths(repo: Path, paths: list[str], message: str) -> PreparedCommit | None:
    """``prepare_blobs`` of only ``paths``: like ``commit_paths``, without moving HEAD."""
    return prepare_blobs(repo, {}, paths, message)


def prepare_merge(repo: Path, sha: str, message: str) -> tuple[PreparedCommit | None, list[str]]:
    """Merge ``sha`` into HEAD (``--no-ff``) into the index and working tree, and build the
    merge commit without moving HEAD. On a conflict, abort the merge and return (None, the
    conflicting files). Any other failure raises GitError. ``land`` aborts the merge if it
    cannot move HEAD; ``settle`` ends it."""
    parent = head(repo)
    result = _run(repo, "merge", "--no-ff", "--no-commit", sha, check=False)
    merging = _run(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False)
    if result.returncode != 0:
        if merging.returncode != 0:
            # No merge in progress: this was not a conflict (bad sha, dirty tree, ...).
            raise GitError(("merge", sha), result.stderr.decode(errors="replace"))
        conflicts = sorted(git(repo, "diff", "--name-only", "--diff-filter=U").splitlines())
        git(repo, "merge", "--abort")
        return None, conflicts
    if merging.returncode != 0:
        raise GitError(("merge", sha), "nothing to merge: HEAD already contains it")
    try:
        tree = git(repo, "write-tree")
        theirs = merging.stdout.decode().strip()
        commit = git(repo, "commit-tree", tree, "-p", parent, "-p", theirs, "-m", message)
    except GitError:
        _run(repo, "merge", "--abort", check=False)
        raise
    return PreparedCommit(commit, parent, message, merge=True), []


def land(repo: Path, prepared: PreparedCommit) -> None:
    """Move HEAD's branch to ``prepared`` if it still points at its parent. ``update-ref``
    is all or nothing: when this raises, HEAD did not move (and a prepared merge is
    aborted)."""
    try:
        git(repo, "update-ref", "-m", f"commit: {prepared.message.splitlines()[0]}", "HEAD",
            prepared.sha, prepared.parent or "")  # fmt: skip
    except GitError:
        discard(repo, prepared)
        raise


def discard(repo: Path, prepared: PreparedCommit) -> None:
    """Drop a prepared commit that will not land: abort a prepared merge (best effort; the
    commit object itself is unreachable garbage)."""
    if prepared.merge:
        _run(repo, "merge", "--abort", check=False)


def settle(repo: Path, prepared: PreparedCommit) -> None:
    """After ``land``: end a prepared merge (its index already matches), or set the real
    index entries of the prepared paths to the new commit; every other entry is left as
    it was."""
    if prepared.merge:
        git(repo, "merge", "--quit")
    elif prepared.paths:
        git(repo, "reset", "-q", prepared.sha, "--", *prepared.paths)


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


def contains(repo: Path, sha: str) -> bool:
    """True if sha is an ancestor of (or equal to) HEAD."""
    result = _run(repo, "merge-base", "--is-ancestor", sha, "HEAD", check=False)
    if result.returncode not in (0, 1):
        raise GitError(("merge-base", "--is-ancestor", sha), result.stderr.decode(errors="replace"))
    return result.returncode == 0


def push(repo: Path, remote: str, refspecs: list[str]) -> bool:
    return _run(repo, "push", remote, *refspecs, check=False).returncode == 0


def push_with_rejections(repo: Path, remote: str, refspecs: list[str]) -> tuple[bool, list[str]]:
    """Push; return (all refs pushed, destination refs the remote rejected, e.g. non-fast-forward)."""
    result = _run(repo, "push", "--porcelain", remote, *refspecs, check=False)
    rejected = []
    for line in result.stdout.decode(errors="replace").splitlines():
        if line.startswith("!"):  # "!\t<src>:<dst>\t[rejected] (reason)"
            rejected.append(line.split("\t")[1].split(":")[-1])
    return result.returncode == 0, rejected


def merge_base(repo: Path, a: str, b: str) -> str:
    return git(repo, "merge-base", a, b)
