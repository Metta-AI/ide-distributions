"""Read-only snapshots of public GitHub repositories, shared by every profile on the host.

``fetch(url, ref)`` (``research source fetch --repo URL [--ref REF]``) accepts only
``https://github.com/<owner>/<repo>`` and a plain ref name (branch, tag or commit; default: the
repository's default branch). Under ``<root>/sources/github.com/<owner>/`` (``games.scope.host_root``)
it keeps:

- ``<repo>.git``: a bare cache holding every commit fetched so far, each fetched shallow
  (``--depth=1``: that commit's files, no history). ``kb renew --game-repo`` can diff two
  fetched commits in it.
- ``<repo>@<sha12>/``: the files of one commit, written once (extracted to a temporary
  directory, then renamed into place) and never changed. Read it; do not edit it. Its
  provenance (repository, ref, commit, time) is ``<repo>@<sha12>.json`` beside it, never inside
  the fetched tree, which the repository controls.

Git runs with an empty configuration (no user or system config, no credential helper, no
terminal prompt), so a private repository fails instead of using a host credential, and no
``insteadOf`` rule can send the request elsewhere. Nothing fetched is executed.
"""

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from ide_research_core.cli import register_group
from ide_research_core.games.scope import host_root
from ide_research_core.io.archive import ArchiveError, extract_git_archive
from ide_research_core.io.locks import file_lock

REPO_URL = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?"
)
REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}")
FETCH_TIMEOUT_S = 600
LOCK_TIMEOUT_S = 900
PRIVATE_HINT = (
    "Only public repositories can be fetched; a private one needs a GitHub credential, which "
    "this profile does not have."
)


class SourceError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def parse_repo(url: str) -> tuple[str, str]:
    match = REPO_URL.fullmatch(url or "")
    if not match or match["repo"] in (".", ".."):
        raise SourceError(
            "bad_arguments", f"{url!r} is not a https://github.com/<owner>/<repo> URL"
        )
    return match["owner"], match["repo"]


def _check_ref(ref: str | None) -> None:
    if ref is not None and (not REF.fullmatch(ref) or ".." in ref or ref.endswith((".lock", "/"))):
        raise SourceError("bad_arguments", f"{ref!r} is not a branch, tag or commit name")


def sources_dir(env: Mapping[str, str] = os.environ) -> Path:
    return host_root(env) / "sources"


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
    }


def _git(*args: str, cwd: Path | None = None, stdout=subprocess.PIPE) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        cwd=cwd,
        env=_git_env(),
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
        timeout=FETCH_TIMEOUT_S,
    )


def _fail(url: str, ref: str | None, result: subprocess.CompletedProcess) -> SourceError:
    stderr = result.stderr.decode(errors="replace").strip()[-500:]
    return SourceError(
        "fetch_failed", f"git could not fetch {url} {ref or '(default branch)'}: {stderr}. {PRIVATE_HINT}"
    )


def record_path(snapshot: Path) -> Path:
    """Where a snapshot's provenance is: beside it, outside the fetched tree."""
    return snapshot.with_name(snapshot.name + ".json")


def _extract(cache: Path, commit: str, target: Path, record: dict) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        extract_git_archive(
            ["-c", "credential.helper=", "--git-dir", str(cache), "archive", "--format=tar", commit],
            temporary,
            env=_git_env(),
        )
    except ArchiveError as error:
        raise SourceError("fetch_failed", f"{record['repo']} at {commit}: {error}") from None
    record_path(target).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.rename(temporary, target)


def fetch(
    url: str,
    ref: str | None = None,
    env: Mapping[str, str] = os.environ,
    remote: str | None = None,
) -> dict:
    """Fetch ``ref`` of the public repository ``url``; return where its files are.

    ``remote`` replaces the clone URL (tests use a local repository); the cache layout still
    comes from ``url``."""
    owner, repo = parse_repo(url)
    _check_ref(ref)
    canonical_url = f"https://github.com/{owner}/{repo}"
    base = sources_dir(env) / "github.com" / owner
    cache = base / f"{repo}.git"
    with file_lock(base / f"{repo}.lock", timeout_s=LOCK_TIMEOUT_S):
        if not (cache / "HEAD").exists():
            _git("init", "--quiet", "--bare", str(cache))
            _git("--git-dir", str(cache), "remote", "add", "origin", remote or canonical_url)
        fetched = _git(
            "--git-dir", str(cache), "fetch", "--quiet", "--depth=1", "origin", ref or "HEAD"
        )
        if fetched.returncode != 0:
            raise _fail(canonical_url, ref, fetched)
        resolved = _git("--git-dir", str(cache), "rev-parse", "--verify", "FETCH_HEAD^{commit}")
        if resolved.returncode != 0:
            raise _fail(canonical_url, ref, resolved)
        commit = resolved.stdout.decode().strip()
        # Keep every fetched commit reachable, so a later gc never drops one a claim cites.
        _git("--git-dir", str(cache), "update-ref", f"refs/fetched/{commit}", commit)
        snapshot = base / f"{repo}@{commit[:12]}"
        if not snapshot.is_dir():
            record = {
                "repo": canonical_url,
                "ref": ref,
                "commit": commit,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            _extract(cache, commit, snapshot, record)
    return {
        "repo": canonical_url,
        "ref": ref,
        "commit": commit,
        "path": str(snapshot),
        "git_dir": str(cache),
    }


# --- ``research source`` ---


def _fetch_command(args) -> int:
    try:
        data = fetch(args.repo, args.ref)
    except SourceError as error:
        print(json.dumps({"ok": False, "error": {"reason": error.reason, "message": str(error)}}))
        return 2 if error.reason == "bad_arguments" else 1
    except (subprocess.TimeoutExpired, OSError) as error:
        print(json.dumps({"ok": False, "error": {"reason": "fetch_failed", "message": str(error)}}))
        return 1
    print(json.dumps({"ok": True, "data": data}, sort_keys=True))
    return 0


def setup(parser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    sub = commands.add_parser(
        "fetch", help="snapshot a public GitHub repository at one commit (read-only)"
    )
    sub.add_argument("--repo", required=True, help="https://github.com/<owner>/<repo>")
    sub.add_argument("--ref", help="branch, tag or commit (default: the default branch)")
    sub.set_defaults(func=_fetch_command)


register_group("source", setup)
