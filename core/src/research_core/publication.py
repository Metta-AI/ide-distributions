"""Publish selected player files as one Git tree. Volume backups own recovery.

The snapshot has no frontend schema. It flattens nested working copies without copying their
Git metadata. The bare publication repository and pending commits stay on the durable volume.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from research_core import layout
from research_core.locks import file_lock

BRANCH = "workspace"
MAX_FILE = 10 * 1024 * 1024
MAX_SNAPSHOT = 48 * 1024 * 1024
EXCLUDED = (
    ".git",
    ".state",
    ".env",
    ".env.*",
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite-*",
    "*.sqlite3",
    "*.sqlite3-*",
    ".ssh",
    ".aws",
    ".softmax",
    ".secrets",
    "*.pem",
    "*.key",
    "credentials.*",
    "auth.json",
    "__pycache__",
    "*.pyc",
    ".venv",
    "venv",
    "node_modules",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    "*.tmp",
    "*.log",
    "*.sock",
    "*.tar",
    "*.tar.gz",
    "*.zip",
)


def git(repo: Path, *args: str, env: dict[str, str], data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", f"--git-dir={repo}", *args], input=data, capture_output=True, env=env, timeout=240
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-2000:]
        for key, value in env.items():
            if key.startswith("GIT_CONFIG_VALUE_") and value.startswith("Authorization: Bearer "):
                detail = detail.replace(value.removeprefix("Authorization: Bearer "), "[redacted]")
        raise RuntimeError(f"git {args[0]} failed (exit {result.returncode}): {detail.strip()}")
    return result.stdout


def files(root: Path):
    """Walk regular files only, respecting nested Git ignores and refusing SQLite by content."""
    walk = [(root.parent, [], [root.name])] if root.is_file() else os.walk(root, followlinks=False)
    for directory, dirs, names in walk:
        directory = Path(directory)
        dirs[:] = sorted(
            d
            for d in dirs
            if not any(fnmatch.fnmatch(d, p) for p in EXCLUDED)
            and not (directory / d).is_symlink()
            and d != "source"
            and not (directory.name == "documents" and d == "repos")
        )
        names = sorted(n for n in names if not any(fnmatch.fnmatch(n, p) for p in EXCLUDED))
        if not root.is_file() and (directory / ".git").exists():
            # Walk this repository separately so Git's own ignore rules apply to its descendants.
            paths = subprocess.run(
                [
                    "git",
                    "-C",
                    str(directory),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ],
                capture_output=True,
                check=True,
                timeout=30,
            ).stdout.split(b"\0")
            dirs[:] = []
            candidates = [directory / os.fsdecode(p) for p in paths if p]
        else:
            candidates = [directory / name for name in names]
        for path in sorted(set(candidates)):
            relative = Path(".") if root.is_file() else path.relative_to(root)
            if any(
                any(fnmatch.fnmatch(part, pattern) for pattern in EXCLUDED)
                for part in relative.parts
            ):
                continue
            if (
                path.is_symlink()
                or any(
                    parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)
                )
                or not path.is_file()
            ):
                continue
            with open(
                path, "rb", opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW)
            ) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"Not a regular publication file: {relative}")
                content = stream.read(MAX_FILE + 1)
                if content.startswith(
                    (b"SQLite format 3\0", b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")
                ):
                    continue
                if len(content) > MAX_FILE:
                    raise ValueError(f"Publication file exceeds 10 MiB: {relative}")
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                    raise ValueError(
                        f"File changed during publication; retry next tick: {relative}"
                    )
            yield relative, content, "100755" if info.st_mode & stat.S_IXUSR else "100644"


def publish(workspace: Path, profiles: list[Path], remote: str, env: dict[str, str]) -> dict:
    state = workspace / ".state" / "publication"
    repo = state / "repository.git"
    with (
        file_lock(state / "publisher.lock", timeout_s=1),
        tempfile.TemporaryDirectory(dir=state) as scratch,
    ):
        subprocess.run(
            ["git", "init", "--bare", str(repo)], env=env, capture_output=True, check=True
        )
        env = {**env, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        # Ignore ambient credentials, hooks, signing and filters. No provider token is persisted.
        git(repo, "read-tree", "--empty", env=env)
        # Only player-owned roots. Never walk the runtime home, toolchain or database folders.
        sources = [
            ("workspace/" + name, workspace / name)
            for name in ("documents", "memory", "skills", "SOUL.md")
            if (workspace / name).exists() and not (workspace / name).is_symlink()
        ]
        for coworld in sorted(workspace.iterdir()):
            if coworld.is_symlink() or not (coworld / ".state" / "store.db").is_file():
                continue
            sources.extend(
                (f"workspace/{coworld.name}/{name}", coworld / name)
                for name in ("code", "documents", "worktrees")
                if (coworld / name).exists() and not (coworld / name).is_symlink()
            )
        sources.extend(
            ("profiles/" + p.name + "/" + name, p / name)
            for p in profiles
            for name in ("memory", "skills", "SOUL.md")
            if (p / name).exists() and not (p / name).is_symlink()
        )
        entries = []
        total = 0
        for prefix, source in sources:
            for relative, content, mode in files(source):
                total += len(content)
                if total > MAX_SNAPSHOT:
                    raise ValueError(
                        "Publication exceeds 48 MiB; exclude generated artifacts before retrying"
                    )
                oid = git(repo, "hash-object", "-w", "--stdin", env=env, data=content).strip()
                path = (
                    prefix if relative == Path(".") else f"{prefix}/{relative.as_posix()}"
                ).encode()
                entries.append(mode.encode() + b" " + oid + b"\t" + path + b"\0")
        git(repo, "update-index", "-z", "--index-info", env=env, data=b"".join(entries))
        tree = git(repo, "write-tree", env=env).strip().decode()
        refs = git(
            repo, "for-each-ref", "--format=%(objectname)", f"refs/heads/{BRANCH}", env=env
        ).splitlines()
        parents = [r.decode() for r in refs]
        remote_ref = git(repo, "ls-remote", remote, f"refs/heads/{BRANCH}", env=env).split()
        if remote_ref:
            git(repo, "fetch", "--no-tags", remote, f"refs/heads/{BRANCH}", env=env)
            fetched = git(repo, "rev-parse", "FETCH_HEAD", env=env).strip().decode()
            if fetched not in parents:
                ancestor = (
                    subprocess.run(
                        [
                            "git",
                            f"--git-dir={repo}",
                            "merge-base",
                            "--is-ancestor",
                            fetched,
                            parents[0],
                        ],
                        env=env,
                        capture_output=True,
                        timeout=30,
                    )
                    if parents
                    else None
                )
                if ancestor is None or ancestor.returncode == 1:
                    parents.append(fetched)
                elif ancestor.returncode != 0:
                    raise RuntimeError("Cannot reconcile publication history")
        if (
            len(parents) == 1
            and git(repo, "rev-parse", f"{parents[0]}^{{tree}}", env=env).strip().decode() == tree
        ):
            commit = parents[0]
        else:
            parent_args = [arg for parent in parents for arg in ("-p", parent)]
            commit = (
                git(
                    repo,
                    "commit-tree",
                    tree,
                    *parent_args,
                    "-m",
                    "Publish workspace snapshot",
                    env=env,
                )
                .strip()
                .decode()
            )
        git(repo, "update-ref", f"refs/heads/{BRANCH}", commit, env=env)
        # The exact snapshot tree wins over external edits, but their commits remain in history.
        # Concurrent remote changes reject the push; the next tick reconciles and retries.
        git(repo, "push", remote, f"refs/heads/{BRANCH}:refs/heads/{BRANCH}", env=env)
        return {"commit": commit, "branch": BRANCH, "files": len(entries), "bytes": total}


def main(args: argparse.Namespace) -> int:
    from research_core.platform.identity import player_token, server
    from research_core.prepare import profile_targets
    from research_core.watcher.hermes_api import read_dotenv

    profile = Path(os.environ["HERMES_HOME"])
    # Hermes can strip secrets from cron subprocesses; the profile owns its credentials.
    profile_env = {**os.environ, **read_dotenv(profile / ".env")}
    ws = layout.from_env(profile_env)
    base = profile_env.get("IDE_GITHUB_BROKER_URL", server(profile_env)).rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError("The GitHub broker requires HTTPS")
    token = player_token(profile_env)
    request = Request(f"{base}/github/workspace-repository", method="POST")
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=30) as response:
        target = json.load(response)
    # Repository names are server-selected; require the fixed branch before using the response.
    from research_core.tools.github import _git_config_env, _repository

    repository = _repository(target["repository"])
    if target["branch"] != BRANCH:
        raise ValueError("Unexpected workspace publication branch")
    remote = f"{base}/github/repositories/{repository}/git"
    env = {k: v for k, v in profile_env.items() if not k.startswith("GIT_")}
    env.update(
        _git_config_env(
            [
                (f"http.{remote}.extraHeader", f"Authorization: Bearer {token}"),
                ("http.followRedirects", "false"),
                ("commit.gpgSign", "false"),
            ]
        )
    )
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="Workspace",
        GIT_AUTHOR_EMAIL="workspace@localhost",
        GIT_COMMITTER_NAME="Workspace",
        GIT_COMMITTER_EMAIL="workspace@localhost",
    )
    profiles = [p for p, _ in profile_targets(profile, profile_env)]
    print(json.dumps(publish(ws.root, profiles, remote, env)))
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "publish", help="Publish player files to the private workspace Git repository"
    )
    parser.set_defaults(run=main)
