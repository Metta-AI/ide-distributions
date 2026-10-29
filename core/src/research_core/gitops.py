"""Git helpers for the Coworld code repository, its worktrees and pinned clones. Stdlib only.

Every function takes the repository (or worktree) path first and runs git through ``git()``,
which fixes the environment: no system or global config, no terminal prompts and a default
author identity, so the helpers behave the same in tests, in the hosted sandbox and on a
developer machine. The task tool may import this module in process, so it stays import-light.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path

DEFAULT_AUTHOR = "IDE <ide@softmax.invalid>"
GIT_TIMEOUT_S = 300

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.MULTILINE)
_LOG_FIELD, _LOG_RECORD = "\x1f", "\x1e"


class GitError(RuntimeError):
    """A git command failed. The message names the command, its exit code and its stderr."""

    def __init__(
        self,
        message: str,
        *,
        command: list[str] | None = None,
        exit_code: int | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.command = list(command or [])
        self.exit_code = exit_code
        self.stderr = stderr


class MergeConflict(Exception):
    """A merge stopped on conflicts; it was aborted and the target branch is unchanged."""

    def __init__(self, files: list[str], *, commit: str, into: str) -> None:
        self.files = list(files)
        self.commit = commit
        self.into = into
        super().__init__(f"merging {commit} into {into} conflicts in: {', '.join(self.files)}")


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every git command runs with; ``extra`` wins over the defaults."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "IDE",
            "GIT_AUTHOR_EMAIL": "ide@softmax.invalid",
            "GIT_COMMITTER_NAME": "IDE",
            "GIT_COMMITTER_EMAIL": "ide@softmax.invalid",
        }
    )
    if extra:
        env.update(extra)
    return env


def git(
    repo: Path | None,
    *args: str,
    check: bool = True,
    env: Mapping[str, str] | None = None,
    timeout_s: float = GIT_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <repo> <args>`` (no ``-C`` when ``repo`` is None) and return the result.

    With ``check`` a non-zero exit raises ``GitError``; a timeout always does.
    """
    command = ["git"] + (["-C", str(repo)] if repo is not None else []) + list(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            env=git_env(env),
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise GitError(
            f"{shlex.join(command)} did not finish within {timeout_s:g} s", command=command
        ) from None
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(
            f"{shlex.join(command)} failed with exit code {result.returncode}: {detail}",
            command=command,
            exit_code=result.returncode,
            stderr=result.stderr,
        )
    return result


# --- repositories and commits -----------------------------------------------------------------


def init_repo(path: Path, *, initial_commit: bool = True, branch: str = "main") -> Path:
    """Create a repository at ``path`` on ``branch``, with an empty first commit by default.

    Idempotent: an existing repository is left as it is (no second initial commit).
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--quiet", "-b", branch)
    if initial_commit and _commit_or_none(path, "HEAD") is None:
        git(path, "commit", "--quiet", "--allow-empty", "-m", "Initialize repository")
    return path


def commit_all(
    repo: Path, message: str, *, author: str = DEFAULT_AUTHOR, allow_empty: bool = False
) -> str:
    """Stage everything (additions, changes, deletions), commit and return the new sha."""
    git(repo, "add", "-A")
    args = ["commit", "--quiet", "-m", message, f"--author={author}"]
    if allow_empty:
        args.append("--allow-empty")
    git(repo, *args)
    return rev_parse(repo, "HEAD")


def rev_parse(repo: Path, ref: str) -> str:
    """The commit sha ``ref`` names (tags are peeled)."""
    return git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").stdout.strip()


def _commit_or_none(repo: Path, ref: str) -> str | None:
    """``rev_parse`` that answers None instead of raising when ``ref`` names no commit."""
    result = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def current_branch(repo: Path) -> str | None:
    """The checked-out branch name, or None when HEAD is detached."""
    result = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def dirty_files(repo: Path) -> list[str]:
    """Paths with uncommitted changes, untracked files included (a directory when whole)."""
    out = git(repo, "status", "--porcelain=v1", "-z", "--no-renames").stdout
    return [entry[3:] for entry in out.split("\0") if entry]


def is_clean(repo: Path) -> bool:
    return not dirty_files(repo)


def ignored_files(repo: Path) -> list[str]:
    """Paths git ignores (a directory when whole), which ``dirty_files`` never reports.

    Repository-relative, as ``git status --ignored`` prints them: a whole ignored directory
    is one entry with a trailing slash.
    """
    out = git(repo, "status", "--ignored", "--porcelain=v1", "-z", "--no-renames").stdout
    return [entry[3:] for entry in out.split("\0") if entry.startswith("!! ")]


def submodule_paths(repo: Path) -> list[str]:
    """The paths ``.gitmodules`` declares as submodules; empty when there is no such file."""
    result = git(
        repo,
        "config",
        "--file",
        ".gitmodules",
        "--get-regexp",
        r"^submodule\..*\.path$",
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.partition(" ")[2] for line in result.stdout.splitlines() if " " in line]


def nested_repositories(repo: Path, *, skip_names: Collection[str] = ()) -> list[str]:
    """Directories below the root with their own ``.git`` that are not declared submodules.

    Git keeps at most a commit pointer for such a directory (an embedded repository), never
    its files, so ``git status`` is clean while a commit of ``repo`` cannot rebuild it. The
    walk does not enter directories named in ``skip_names`` (caches) nor a nested repository
    once found, and never follows symlinks. Paths are repository-relative, no trailing slash.
    """
    root = Path(repo)
    submodules = set(submodule_paths(root))
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        relative = Path(current).relative_to(root).as_posix()
        if relative != "." and (".git" in dirs or ".git" in files):
            if relative not in submodules:
                found.append(relative)
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d != ".git" and d not in skip_names)
    return found


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise GitError(
        f"git merge-base --is-ancestor {ancestor} {descendant} failed with exit code "
        f"{result.returncode}: {result.stderr.strip()}",
        exit_code=result.returncode,
        stderr=result.stderr,
    )


def commit_on_branch(repo: Path, sha: str, branch: str) -> bool:
    """Whether ``sha`` exists and is reachable from ``refs/heads/<branch>``."""
    if any(_commit_or_none(repo, ref) is None for ref in (sha, f"refs/heads/{branch}")):
        return False
    return is_ancestor(repo, sha, f"refs/heads/{branch}")


def tag(repo: Path, name: str, sha: str) -> None:
    """Create the lightweight tag ``name`` at ``sha``; a tag already there is left alone."""
    if _commit_or_none(repo, f"refs/tags/{name}") == rev_parse(repo, sha):
        return
    git(repo, "tag", name, sha)


def log(repo: Path, ref: str = "HEAD", limit: int = 20) -> list[dict]:
    """The newest ``limit`` commits reachable from ``ref``: sha, author, date (ISO), subject."""
    fmt = _LOG_FIELD.join(("%H", "%an", "%aI", "%s")) + _LOG_RECORD
    out = git(repo, "log", f"-n{limit}", f"--format={fmt}", ref, "--").stdout
    entries = []
    for record in out.split(_LOG_RECORD):
        if not record.strip():
            continue
        sha, author, date, subject = record.strip("\n").split(_LOG_FIELD, 3)
        entries.append({"sha": sha, "author": author, "date": date, "subject": subject})
    return entries


# --- worktrees --------------------------------------------------------------------------------


def list_worktrees(repo: Path) -> list[dict]:
    """Every worktree of the repository: ``{path, head, branch (None when detached), bare}``."""
    out = git(repo, "worktree", "list", "--porcelain").stdout
    worktrees: list[dict] = []
    for block in out.strip().split("\n\n"):
        if not block.strip():
            continue
        entry: dict = {"path": None, "head": None, "branch": None, "bare": False}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                entry["path"] = Path(value)
            elif key == "HEAD":
                entry["head"] = value
            elif key == "branch":
                entry["branch"] = value.removeprefix("refs/heads/")
            elif key == "bare":
                entry["bare"] = True
        worktrees.append(entry)
    return worktrees


def worktree_add(repo: Path, path: Path, *, branch: str, base: str = "main") -> Path:
    """Check ``branch`` out at ``path`` as a worktree, creating the branch from ``base`` if new.

    A worktree already at ``path`` on that branch is reused; one on another branch is refused.
    """
    path = Path(path)
    git(repo, "worktree", "prune")
    for worktree in list_worktrees(repo):
        if worktree["path"].resolve() != path.resolve() or worktree["bare"]:
            continue
        if worktree["branch"] == branch:
            return path
        at = worktree["branch"] or "a detached HEAD"
        raise GitError(f"{path} is a worktree of {at}, not {branch}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if _commit_or_none(repo, f"refs/heads/{branch}") is not None:
        git(repo, "worktree", "add", "--quiet", str(path), branch)
    else:
        git(repo, "worktree", "add", "--quiet", "-b", branch, str(path), base)
    return path


def worktree_remove(repo: Path, path: Path, *, force: bool = False) -> None:
    """Remove the worktree at ``path`` (``force`` discards local changes) and prune records."""
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(path)]
    git(repo, *args)
    git(repo, "worktree", "prune")


def merge_no_ff(repo: Path, commit: str, *, message: str, into: str = "main") -> str:
    """Merge ``commit`` into branch ``into`` with a merge commit and return its sha.

    The merge runs in a temporary worktree so no agent's worktree is touched; the branch is
    then fast-forwarded where it is checked out (or its ref moved when it is not). Conflicts
    raise ``MergeConflict`` after a clean abort and leave ``into`` where it was. A commit that
    is already merged returns the current tip.
    """
    old_tip = rev_parse(repo, f"refs/heads/{into}")
    with tempfile.TemporaryDirectory(prefix="ide-merge-") as temp:
        worktree = Path(temp) / "merge"
        git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), old_tip)
        try:
            merged = git(worktree, "merge", "--no-ff", "-m", message, commit, check=False)
            if merged.returncode != 0:
                conflicts = git(worktree, "diff", "--name-only", "--diff-filter=U").stdout.split()
                git(worktree, "merge", "--abort", check=False)
                if conflicts:
                    raise MergeConflict(conflicts, commit=commit, into=into)
                raise GitError(
                    f"git merge --no-ff {commit} failed with exit code {merged.returncode}: "
                    f"{(merged.stderr or merged.stdout).strip()}",
                    exit_code=merged.returncode,
                    stderr=merged.stderr,
                )
            new_tip = rev_parse(worktree, "HEAD")
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            git(repo, "worktree", "prune", check=False)
    if new_tip == old_tip:
        return old_tip
    checked_out = [w for w in list_worktrees(repo) if w["branch"] == into and not w["bare"]]
    if checked_out:
        git(checked_out[0]["path"], "merge", "--quiet", "--ff-only", new_tip)
    else:
        git(repo, "update-ref", f"refs/heads/{into}", new_tip, old_tip)
    return new_tip


# --- file contents and line ranges ------------------------------------------------------------


def show_file(repo: Path, ref: str, path: str) -> str:
    """The text of ``path`` at ``ref`` (a commit, branch or tag), straight from the repository."""
    return git(repo, "show", f"{ref}:{path}").stdout


def show_lines(repo: Path, path: str, a: int, b: int, sha: str) -> list[str]:
    """Lines ``a``..``b`` (1-based, inclusive) of ``path`` at commit ``sha``."""
    _check_range(a, b)
    content = git(repo, "show", f"{sha}:{path}").stdout
    return content.splitlines()[a - 1 : b]


def lines_changed_since(repo: Path, path: str, a: int, b: int, sha: str) -> bool:
    """Whether lines ``a``..``b`` of ``path`` at ``sha`` differ at HEAD.

    Compares the old side of each ``git diff sha HEAD -- path`` hunk with the range. A pure
    insertion counts only when it lands between two cited lines. A path absent at ``sha``
    counts as changed: the citation cannot be checked.
    """
    _check_range(a, b)
    if git(repo, "cat-file", "-e", f"{sha}:{path}", check=False).returncode != 0:
        return True
    diff = git(repo, "diff", "-U0", "--no-renames", sha, "HEAD", "--", path).stdout
    for start_text, count_text in _HUNK.findall(diff):
        start, count = int(start_text), 1 if count_text == "" else int(count_text)
        if count == 0:
            if a <= start < b:
                return True
        elif start <= b and start + count - 1 >= a:
            return True
    return False


def _check_range(a: int, b: int) -> None:
    if a < 1 or b < a:
        raise ValueError(f"not a line range: {a}-{b} (1-based, first <= last)")


# --- clones -----------------------------------------------------------------------------------


def clone_pinned(url: str, dest: Path, commit: str, *, depth: int = 1) -> str:
    """Clone ``url`` into ``dest`` and detach at ``commit``; return the full sha checked out.

    A shallow clone takes the remote's default branch; when ``commit`` is not in it the commit
    is fetched by sha (depth ``depth``). An existing clone at ``dest`` is reused and updated.
    """
    dest = Path(dest)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(None, "clone", "--quiet", f"--depth={depth}", "--no-tags", url, str(dest))
    if _commit_or_none(dest, commit) is None:
        git(dest, "fetch", "--quiet", f"--depth={depth}", "--no-tags", "origin", commit)
    git(dest, "checkout", "--quiet", "--detach", commit)
    return rev_parse(dest, "HEAD")
