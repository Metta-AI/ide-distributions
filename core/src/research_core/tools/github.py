"""The ``github`` tool (Orchestrator): read-only GitHub access through the Softmax broker.

The user connected GitHub on softmax.com; the workspace never holds a GitHub token. The broker
at ``{COGAMES_API_URL}/github/`` lists the repositories the user granted
(``repositories {page?}``) and serves them to git (``clone {repository, commit?}``) against the
Player bearer token. The clone passes that token to git through ``GIT_CONFIG_COUNT`` entries
for one command (an ``http.<broker url>.extraHeader``), so nothing lands in ``.git/config``.
Checkouts are shallow, detached at the requested commit and cached by commit under
``<coworld>/documents/repos/<owner>-<name>-<sha7>``: the same commit is cloned once.

A failing broker call or clone reports the ``github`` capability (A4.1), which opens the one
repair Task; argument problems (a bad repository name or sha) are plain refusals.

Environment overrides, for tests and local work:

- ``IDE_GITHUB_BROKER_URL``: the broker base instead of ``COGAMES_API_URL`` (``https``, or
  ``http`` to a loopback address);
- ``IDE_GITHUB_MIRROR``: a directory or ``file://`` URL holding ``<owner>/<name>`` repositories;
  clones come from there with no broker and no token.

Runs in the workspace environment.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from research_core import gitops
from research_core.platform.errors import PlatformError
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolContext, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
CAPABILITY = "github"
BROKER_URL_ENV = "IDE_GITHUB_BROKER_URL"
MIRROR_ENV = "IDE_GITHUB_MIRROR"
USER_AGENT = "softmax-ide-research"
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
MAX_PAGE = 1000
LIST_TIMEOUT_S = 30
CLONE_TIMEOUT_S = 240
NEXT = "Use read_file and search_files on this checkout; cite paths and the commit."

_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def handle(call: ToolCall) -> dict:
    if call.action == "repositories":
        return repositories(call)
    return clone(call)


# --- repositories -------------------------------------------------------------------------


def repositories(call: ToolCall) -> dict:
    ctx = call.ctx
    page = call.get("page") or 1
    if not 1 <= page <= MAX_PAGE:
        raise ToolError(f"page must be between 1 and {MAX_PAGE}")
    token = _player_token(ctx)
    url = f"{_broker(ctx)}/github/repositories?page={page}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    command = f"GET {url}"
    try:
        with urlopen(request, timeout=LIST_TIMEOUT_S) as response:
            body = json.load(response)
    except HTTPError as error:
        detail = _redact(error.read().decode("utf-8", errors="replace")[-2000:], token)
        raise _failure(
            ctx,
            f"the GitHub broker answered {error.code} for {url}: {detail}",
            command=command,
            output=f"HTTP {error.code}\n{detail}",
            status=error.code,
        ) from error
    except (URLError, TimeoutError, ValueError) as error:
        detail = _redact(f"{type(error).__name__}: {error}", token)
        raise _failure(ctx, f"{url} failed: {detail}", command=command, output=detail) from error
    return {
        "page": page,
        "repositories": body,
        "next": "Pass page + 1 while the list is full; clone with repository owner/name.",
    }


# --- clone --------------------------------------------------------------------------------


def clone(call: ToolCall) -> dict:
    ctx = call.ctx
    repository = _repository(call.get("repository"))
    commit = _commit(call.get("commit"))
    owner, name = repository.split("/")
    root = ctx.coworld().documents / "repos"
    if commit is not None:
        dest = root / f"{owner}-{name}-{commit[:7]}"
        if (dest / ".git").exists() and _head(dest) == commit:
            return _result(repository, dest, commit, cached=True)
    source, env, token = _source(ctx, repository)
    try:
        if commit is not None:
            sha = _clone_pinned(source, dest, commit, env)
        else:
            staging = root / f".clone-{owner}-{name}-{os.getpid()}"
            shutil.rmtree(staging, ignore_errors=True)
            sha = _clone_pinned(source, staging, None, env)
            dest = root / f"{owner}-{name}-{sha[:7]}"
            if (dest / ".git").exists():
                shutil.rmtree(staging)
                return _result(repository, dest, sha, cached=True)
            staging.rename(dest)
    except gitops.GitError as error:
        detail = _redact(f"{error}\n{error.stderr}".strip(), token)
        raise _failure(
            ctx,
            f"cloning {repository} failed: {detail}",
            command=_redact(" ".join(error.command) or f"git clone {source}", token),
            output=detail,
            exit_code=error.exit_code,
        ) from error
    return _result(repository, dest, sha, cached=False)


def _clone_pinned(source: str, dest: Path, commit: str | None, env: dict[str, str]) -> str:
    """A shallow clone at ``dest``, detached at ``commit`` when given; the sha checked out.

    ``gitops.clone_pinned`` has the same shape but no way to pass the one-command authorisation
    env, which must not become process-wide, so the three git calls are spelled out here.
    """
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        gitops.git(
            None,
            "clone",
            "--quiet",
            "--depth=1",
            "--no-tags",
            "--no-recurse-submodules",
            "--",
            source,
            str(dest),
            env=env,
            timeout_s=CLONE_TIMEOUT_S,
        )
    if commit is not None:
        have = gitops.git(
            dest, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", check=False
        )
        if have.returncode != 0:
            gitops.git(
                dest,
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "origin",
                commit,
                env=env,
                timeout_s=CLONE_TIMEOUT_S,
            )
        gitops.git(dest, "checkout", "--quiet", "--detach", commit, env=env)
    else:
        gitops.git(dest, "checkout", "--quiet", "--detach", env=env)
    return gitops.rev_parse(dest, "HEAD")


def _source(ctx: ToolContext, repository: str) -> tuple[str, dict[str, str], str | None]:
    """Where to clone from and the git config env for it: the mirror, else the broker."""
    mirror = ctx.env.get(MIRROR_ENV)
    if mirror:
        base = mirror if mirror.startswith("file://") else Path(mirror).resolve().as_uri()
        # The local upload-pack inherits this env, so a pinned commit older than the mirror's
        # tip can be fetched by sha, as GitHub allows.
        allow = [("uploadpack.allowReachableSHA1InWant", "true")]
        return f"{base.rstrip('/')}/{repository}", _git_config_env(allow), None
    token = _player_token(ctx)
    url = f"{_broker(ctx)}/github/repositories/{repository}/git"
    entries = [
        (f"http.{url}.extraHeader", f"Authorization: Bearer {token}"),
        ("http.followRedirects", "false"),
        ("http.userAgent", USER_AGENT),
    ]
    return url, _git_config_env(entries), token


def _git_config_env(entries: list[tuple[str, str]]) -> dict[str, str]:
    """``GIT_CONFIG_COUNT`` entries for one git command: no hooks, no credential helper, no
    LFS smudge, plus the caller's (the broker authorisation)."""
    entries = [("core.hooksPath", os.devnull), ("credential.helper", ""), *entries]
    env = {"GIT_CONFIG_COUNT": str(len(entries)), "GIT_LFS_SKIP_SMUDGE": "1"}
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


# --- helpers ------------------------------------------------------------------------------


def _repository(value: object) -> str:
    text = str(value or "").strip()
    text = text.removeprefix("https://github.com/").rstrip("/").removesuffix(".git")
    if not _REPOSITORY.match(text) or text.split("/")[1] in (".", ".."):
        raise ToolError(
            f"repository must be owner/name or an https://github.com/owner/name URL, not {value!r}"
        )
    return text


def _commit(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if not _SHA.match(text):
        raise ToolError(f"commit must be a full 40-character git sha, not {value!r}")
    return text


def _broker(ctx: ToolContext) -> str:
    from research_core.platform import identity

    base = (ctx.env.get(BROKER_URL_ENV) or identity.server(ctx.env)).rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK):
        raise ToolError(f"the GitHub broker must be reached over https, not {base}")
    return base


def _player_token(ctx: ToolContext) -> str:
    from research_core.platform import identity

    try:
        return identity.player_token(ctx.env)
    except PlatformError as error:
        raise ToolError([error.detail], route="GET /github/repositories") from error


def _head(repo: Path) -> str | None:
    result = gitops.git(repo, "rev-parse", "HEAD", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _result(repository: str, path: Path, commit: str, *, cached: bool) -> dict:
    return {
        "repository": f"https://github.com/{repository}",
        "path": str(path),
        "commit": commit,
        "cached": cached,
        "next": NEXT,
    }


def _redact(text: str, token: str | None) -> str:
    return text.replace(token, "[redacted]") if token else text


def _failure(ctx: ToolContext, problem: str, *, command: str, output: str, **context) -> ToolError:
    """Mark the ``github`` capability broken (opening or joining its repair Task) and refuse."""
    from research_core.store import capabilities

    report = capabilities.report_failure(
        ctx.store, ctx.actor, CAPABILITY, output=output, command=command
    )
    task = report.get("maintenance_task")
    return ToolError(
        [problem],
        capability=CAPABILITY,
        maintenance_task=None if task is None else task["id"],
        **context,
    )


SPEC = tool(
    ToolSpec(
        name="github",
        description=(
            "Read-only GitHub access through the user's Softmax GitHub connection: list the "
            "repositories the user granted and clone one at a pinned commit into the Coworld's "
            "documents/repos/ (cached by commit). No token reaches the workspace; no push."
        ),
        handler=handle,
        roles=ORCHESTRATOR,
        executor="workspace",
        actions=(
            ActionSpec(
                "repositories",
                "List the repositories the user granted, one page at a time.",
                {"page": a.integer("Page number from 1 (default 1).")},
            ),
            ActionSpec(
                "clone",
                "Clone a repository (shallow) at a commit, or its default branch tip.",
                {
                    "repository": a.string("owner/name or an https://github.com/owner/name URL."),
                    "commit": a.string("A full 40-character sha to check out (detached)."),
                },
                ("repository",),
            ),
        ),
    )
)
