"""Bootstrap: the first step of every queue-keeper run (Overview C1, amendments A2, A6, A7).

Local setup only, idempotent, and it never creates cards (admission does, after preflight):

1. The venv is the launcher's job (``autoresearch.launch``), already done when this runs.
2. Config migrations (C9): ``config_migrations.apply(runner, home, env)``.
3. The CLI shim ``$HERMES_HOME/bin/autoresearch`` (only that file; ``bin/`` also holds
   Hermes's ``tirith`` and is never cleaned, A6), and in the profile ``.env``
   ``AUTORESEARCH_CLI`` (the shim's absolute path) and ``HERMES_WRITE_SAFE_ROOT`` (canonical).
4. The canonical workspace. When it has no git repository: clone it from
   ``AUTORESEARCH_WORKSPACE_REMOTE`` (a relative path is relative to ``HERMES_HOME``) if the
   remote has a ``main`` (a new host restoring from the remote), else seed it from ``games/<game>/seed/`` with ``AGENTS.md`` (from
   ``doctrine/research-agent.md``, or a placeholder until Plan 03 Task 4.1 ships doctrine),
   ``.gitignore`` and ``.supervisor/ledger/``, then commit and push ``main``. Only a
   successful ``ls-remote`` decides between the two: when the remote cannot be read
   (network, auth), bootstrap fails and the next run retries, so a host never seeds a
   history that diverges from the remote's. An existing
   workspace only gets missing ``.gitignore`` lines (committed) and an ``origin`` remote when
   it has none. Seeded, cloned and existing workspaces get ``AGENTS.md`` re-seeded (one
   canonical commit) when ``doctrine/VERSION`` differs from the version in its header.
   Every run also makes sure ``comms/inbox/`` and ``comms/outbox/`` exist (empty
   directories, untracked) for external agents. An external agent may write
   ``comms/inbox/`` before the first bootstrap (the Softmax IDE bridge's ``deliver`` creates
   the directory when a coach clicks a research button early). Seed and clone set those
   entries aside under ``HERMES_HOME`` (seed looks again right before its commit, for files
   written meanwhile) and put them back untracked afterwards, so the seed commit never
   contains them and queue-keeper's routing adopts them like any direct write.
5. The board (A2): ``queue.ensure_board()`` when ``queue.board_exists()`` is False, and then a
   new board epoch in ``ledger/board.json`` (committed).

A failure is returned in ``BootstrapResult.error``; queue-keeper then stops with exit 1.
"""

import re
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.docs import write_text
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.runctx import RunContext
from autoresearch.runtime import config_migrations
from autoresearch.runtime.config_migrations import Runner
from autoresearch.runtime.ledger import canonical_lock, ledger_of
from autoresearch.runtime.rebuild import new_board_epoch

REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ and doctrine/ live here
REMOTE_ENV = "AUTORESEARCH_WORKSPACE_REMOTE"
GAME_ENV = "AUTORESEARCH_GAME"
DEFAULT_GAME = "gota"
REMOTE = "origin"
BRANCH = "main"
# .scratch/ holds a card worker's scratch files (skills write there, never /tmp or $HOME), so
# card finish does not commit them.
GITIGNORE_LINES = (".supervisor/runtime/", ".worktrees/", ".scratch/")
DOCTRINE_DIR = Path("doctrine")
DOCTRINE_FILES = ("research-agent.md", "communication.md")
VERSION_HEADER = re.compile(r"\A<!-- doctrine-version: (\S+) -->")
AGENTS_PLACEHOLDER = (
    "# Research agent doctrine\n\n"
    "Doctrine is not shipped yet: it arrives with Plan 03 Task 4.1, and bootstrap then seeds\n"
    "it here. Until then, follow the card briefing and the skills.\n"
)
COMMITTER = ("autoresearch", "autoresearch@localhost")
INBOX = Path("comms") / "inbox"


class BootstrapError(RuntimeError):
    pass


@dataclass
class BootstrapResult:
    ok: bool = True
    error: str | None = None
    board_created: bool = False
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def shim_path(home: Path) -> Path:
    return Path(home) / "bin" / "autoresearch"


def shim_text(home: Path) -> str:
    python = Path(home) / "local" / "venv" / "bin" / "python"
    return f'#!/bin/sh\nexec "{python}" -m autoresearch "$@"\n'


def _write_shim(home: Path, result: BootstrapResult) -> None:
    path = shim_path(home)
    wanted = shim_text(home)
    if not path.exists() or path.read_text() != wanted:
        write_text(path, wanted)
        result.actions.append(f"wrote {path}")
    path.chmod(0o755)


def set_env_lines(path: Path, values: dict[str, str]) -> bool:
    """Set ``KEY=value`` lines in a dotenv file: replace a line for the key in place, else
    append. Other lines are kept as they are. Returns True if the file changed."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    updated = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    updated += [f"{key}={value}" for key, value in remaining.items()]
    if updated == lines:
        return False
    write_text(path, "\n".join(updated) + "\n")
    path.chmod(0o600)
    return True


def resolve_remote(remote: str, home: Path) -> str:
    """A relative local path is relative to ``HERMES_HOME`` (never to the job's working
    directory); URLs (``scheme://``, ``user@host:path``) and absolute paths are unchanged."""
    if "://" in remote or Path(remote).is_absolute():
        return remote
    if ":" in remote.split("/", 1)[0]:  # scp-like host:path
        return remote
    return str((Path(home) / remote).resolve())


def _remote_has_main(remote: str, home: Path) -> bool:
    """Whether ``remote`` has a ``main``, answered only by a successful ``ls-remote``. Any
    git failure (network, auth, missing repository) raises: seeding a fresh workspace when
    the remote may hold one would create a divergent history. Bootstrap fails and the next
    run retries."""
    try:
        output = gitops.git(Path(home), "ls-remote", remote, f"refs/heads/{BRANCH}")
    except gitops.GitError as error:
        raise BootstrapError(f"cannot read {remote} (ls-remote failed): {error}") from error
    return bool(output.strip())


def _ensure_committer(canonical: Path) -> None:
    """Canonical commits need an identity; hosts may have no global git identity."""
    for key, value in zip(("user.name", "user.email"), COMMITTER, strict=True):
        try:
            gitops.git(canonical, "config", key)
        except gitops.GitError:
            gitops.git(canonical, "config", key, value)


@contextmanager
def _early_inbox_set_aside(
    canonical: Path, result: BootstrapResult
) -> Iterator[Callable[[], None]]:
    """Move what an external agent wrote to ``comms/inbox/`` before the first bootstrap
    (dotfiles too: the bridge writes ``.<id>.json.tmp``, then renames it) out of ``canonical``
    while it is seeded or cloned, then move it back (names and mtimes kept), untracked, for
    routing to adopt. Yields a sweep that moves whatever arrived since; call it right before
    a commit that could sweep the inbox in. The holding directory is a sibling of
    ``canonical`` (same filesystem, so a move is a rename). An entry whose name is taken
    after a clone is left in the holding directory with a warning, never overwriting the
    tracked file."""
    inbox = canonical / INBOX
    canonical.parent.mkdir(parents=True, exist_ok=True)
    holding = Path(tempfile.mkdtemp(prefix=".workspace-early-inbox-", dir=canonical.parent))

    def sweep() -> None:
        for entry in sorted(inbox.iterdir()) if inbox.is_dir() else []:
            shutil.move(entry, holding / entry.name)

    sweep()
    try:
        yield sweep
    finally:
        held = sorted(holding.iterdir())
        if held:
            inbox.mkdir(parents=True, exist_ok=True)
            kept = 0
            for entry in held:
                if not (inbox / entry.name).exists():
                    shutil.move(entry, inbox / entry.name)
                    kept += 1
            result.actions.append(f"kept {kept} inbox files written before bootstrap for adoption")
        left = sorted(entry.name for entry in holding.iterdir())
        if left:
            result.warnings.append(
                f"inbox files {left} written before bootstrap clash with tracked files; "
                f"left in {holding}"
            )
        else:
            holding.rmdir()


def _clone(canonical: Path, remote: str, result: BootstrapResult) -> None:
    """Fetch an existing workspace into ``canonical`` (which may already hold runtime files)."""
    with _early_inbox_set_aside(canonical, result):
        canonical.mkdir(parents=True, exist_ok=True)
        gitops.git(canonical, "init", "-b", BRANCH)
        gitops.git(canonical, "remote", "add", REMOTE, remote)
        gitops.git(canonical, "fetch", REMOTE)
        gitops.git(canonical, "checkout", "-B", BRANCH, f"{REMOTE}/{BRANCH}")
        gitops.git(canonical, "branch", f"--set-upstream-to={REMOTE}/{BRANCH}", BRANCH)
        _ensure_committer(canonical)
    result.actions.append(f"cloned canonical from {remote}")


def _doctrine_version(repo_root: Path) -> str | None:
    version_file = repo_root / DOCTRINE_DIR / "VERSION"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None


def _agents_md(repo_root: Path) -> str:
    """The workspace ``AGENTS.md``: a version header, then the doctrine files."""
    version = _doctrine_version(repo_root)
    if version is None:
        return AGENTS_PLACEHOLDER
    header = (
        f"<!-- doctrine-version: {version} -->\n"
        "# Autoresearch standing instructions\n\n"
        f'Doctrine version {version}. Run the autoresearch CLI as `"$AUTORESEARCH_CLI"`.\n\n'
    )
    parts = [
        (repo_root / DOCTRINE_DIR / name).read_text(encoding="utf-8") for name in DOCTRINE_FILES
    ]
    return header + "\n".join(parts)


def _reseed_agents_md(canonical: Path, repo_root: Path, result: BootstrapResult) -> None:
    """Rewrite ``AGENTS.md`` when ``doctrine/VERSION`` differs from the header's version."""
    wanted = _doctrine_version(repo_root)
    if wanted is None:
        return
    path = canonical / "AGENTS.md"
    current = VERSION_HEADER.match(path.read_text(encoding="utf-8")) if path.exists() else None
    if current and current.group(1) == wanted:
        return
    with canonical_lock(canonical):
        write_text(path, _agents_md(repo_root))
        gitops.commit_paths(canonical, ["AGENTS.md"], f"bootstrap: doctrine version {wanted}")
    result.actions.append(f"re-seeded AGENTS.md at doctrine version {wanted}")


def _seed(canonical: Path, game: str, remote: str, repo_root: Path, result: BootstrapResult):
    seed = repo_root / "games" / game / "seed"
    if not seed.is_dir():
        raise BootstrapError(f"no seed for game {game!r} at {seed}")
    with _early_inbox_set_aside(canonical, result) as sweep_inbox:
        canonical.mkdir(parents=True, exist_ok=True)
        gitops.git(canonical, "init", "-b", BRANCH)
        _ensure_committer(canonical)
        shutil.copytree(seed, canonical, dirs_exist_ok=True)
        write_text(canonical / "AGENTS.md", _agents_md(repo_root))
        write_text(canonical / ".gitignore", "".join(f"{line}\n" for line in GITIGNORE_LINES))
        (ledger_of(canonical) / ".gitkeep").parent.mkdir(parents=True, exist_ok=True)
        (ledger_of(canonical) / ".gitkeep").touch()
        gitops.git(canonical, "remote", "add", REMOTE, remote)
        sweep_inbox()  # the bridge may have written since the first sweep
        gitops.commit_all(canonical, f"bootstrap: seed workspace from games/{game}/seed")
    result.actions.append(f"seeded canonical from games/{game}/seed")
    if not gitops.push(canonical, REMOTE, [f"HEAD:refs/heads/{BRANCH}"]):
        result.warnings.append(f"initial push to {remote} failed; queue-keeper retries it")


def _update_existing(canonical: Path, remote: str | None, result: BootstrapResult) -> None:
    path = canonical / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in lines]
    if missing:
        with canonical_lock(canonical):
            write_text(path, "\n".join([*lines, *missing]) + "\n")
            gitops.commit_paths(canonical, [".gitignore"], "bootstrap: .gitignore runtime dirs")
        result.actions.append(f"added {missing} to .gitignore")
    remotes = gitops.git(canonical, "remote").split()
    if REMOTE not in remotes:
        if remote:
            gitops.git(canonical, "remote", "add", REMOTE, remote)
            result.actions.append(f"added remote {REMOTE} {remote}")
        else:
            result.warnings.append(f"canonical has no {REMOTE!r} remote and {REMOTE_ENV} is unset")
    elif remote and gitops.git(canonical, "remote", "get-url", REMOTE) != remote:
        result.warnings.append(
            f"canonical {REMOTE} is {gitops.git(canonical, 'remote', 'get-url', REMOTE)}, "
            f"not {REMOTE_ENV}={remote}; left unchanged"
        )


def _ensure_workspace(
    ctx: RunContext, env: Mapping[str, str], repo_root: Path, result: BootstrapResult
) -> None:
    canonical = ctx.canonical
    remote = env.get(REMOTE_ENV) or None
    if remote:
        remote = resolve_remote(remote, ctx.hermes_home)
    if (canonical / ".git").exists():
        _update_existing(canonical, remote, result)
        _reseed_agents_md(canonical, repo_root, result)
        return
    if not remote:
        raise BootstrapError(f"{REMOTE_ENV} is not set: it is required to create the workspace")
    if _remote_has_main(remote, ctx.hermes_home):
        _clone(canonical, remote, result)
        _reseed_agents_md(canonical, repo_root, result)
    else:
        _seed(canonical, env.get(GAME_ENV) or DEFAULT_GAME, remote, repo_root, result)


def _ensure_comms_dirs(canonical: Path) -> None:
    """``comms/inbox/`` and ``comms/outbox/`` exist from the first run (empty, so git does not
    track them). An external agent reads a missing outbox as "not initialized yet" and the
    Softmax IDE bridge waits until it exists; the first publication may be hours away."""
    for directory in (Layout(canonical).comms_inbox, Layout(canonical).comms_outbox):
        directory.mkdir(parents=True, exist_ok=True)


def _ensure_board(ctx: RunContext, queue: Queue, result: BootstrapResult) -> None:
    board_exists = getattr(queue, "board_exists", None)
    ensure_board = getattr(queue, "ensure_board", None)
    if board_exists is None or ensure_board is None or board_exists():
        return
    ensure_board()
    with canonical_lock(ctx.canonical):
        epoch = new_board_epoch(ctx.canonical, "board created by bootstrap")
    result.board_created = True
    result.actions.append(f"created the board (epoch {epoch})")


def _push(canonical: Path, result: BootstrapResult) -> None:
    """Push the epoch commit now (queue-keeper's push step would too, but ``--bootstrap-only``
    stops before it)."""
    has_remote = REMOTE in gitops.git(canonical, "remote").split()
    if has_remote and not gitops.push(canonical, REMOTE, [f"HEAD:refs/heads/{BRANCH}"]):
        result.warnings.append("push of the board epoch failed; queue-keeper retries it")


def bootstrap(
    ctx: RunContext,
    env: Mapping[str, str],
    queue: Queue,
    *,
    runner: Runner = config_migrations.hermes_runner,
    repo_root: Path = REPO_ROOT,
) -> BootstrapResult:
    """Run bootstrap (module docstring); never raises for an expected failure."""
    result = BootstrapResult()
    home = ctx.hermes_home
    try:
        for argv in config_migrations.apply(runner, home, env):
            result.actions.append("hermes " + " ".join(argv))
        _write_shim(home, result)
        env_values = {
            "AUTORESEARCH_CLI": str(shim_path(home)),
            "HERMES_WRITE_SAFE_ROOT": str(ctx.canonical),
        }
        if set_env_lines(home / ".env", env_values):
            result.actions.append("updated the profile .env")
        _ensure_workspace(ctx, env, Path(repo_root), result)
        _ensure_comms_dirs(ctx.canonical)
        _ensure_board(ctx, queue, result)
        if result.board_created:
            _push(ctx.canonical, result)
    except Exception as error:  # noqa: BLE001 - any failure stops the run with a clear error
        result.ok = False
        result.error = f"{type(error).__name__}: {error}"
    return result
