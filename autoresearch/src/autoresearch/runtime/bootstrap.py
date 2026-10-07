"""Bootstrap: the first step of every queue-keeper run (Overview C1, amendments A2, A6, A7).

Local setup only, idempotent, and it never creates cards (admission does, after preflight):

1. The venv is the launcher's job (``autoresearch.launch``), already done when this runs.
2. Config migrations (C9): ``config_migrations.apply(runner, home)``.
3. The CLI shim ``$HERMES_HOME/bin/autoresearch`` (only that file; ``bin/`` also holds
   Hermes's ``tirith`` and is never cleaned, A6), and in the profile ``.env``
   ``AUTORESEARCH_CLI`` (the shim's absolute path) and ``HERMES_WRITE_SAFE_ROOT`` (canonical).
4. The canonical workspace. When it has no git repository: clone it from
   ``AUTORESEARCH_WORKSPACE_REMOTE`` if the remote has a ``main`` (a new host restoring from
   the remote), else seed it from ``games/<game>/seed/`` with ``AGENTS.md`` (from
   ``doctrine/research-agent.md``, or a placeholder until Plan 03 Task 4.1 ships doctrine),
   ``.gitignore`` and ``.supervisor/ledger/``, then commit and push ``main``. An existing
   workspace only gets missing ``.gitignore`` lines (committed) and an ``origin`` remote when
   it has none.
5. The board (A2): ``queue.ensure_board()`` when ``queue.board_exists()`` is False, and then a
   new board epoch in ``ledger/board.json`` (committed).

A failure is returned in ``BootstrapResult.error``; queue-keeper then stops with exit 1.
"""

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.docs import write_text
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
GITIGNORE_LINES = (".supervisor/runtime/", ".worktrees/")
DOCTRINE_FILE = Path("doctrine") / "research-agent.md"
AGENTS_PLACEHOLDER = (
    "# Research agent doctrine\n\n"
    "Doctrine is not shipped yet: it arrives with Plan 03 Task 4.1, and bootstrap then seeds\n"
    "it here. Until then, follow the card briefing and the skills.\n"
)
COMMITTER = ("autoresearch", "autoresearch@localhost")


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


def _remote_has_main(remote: str) -> bool:
    try:
        output = gitops.git(Path.cwd(), "ls-remote", remote, f"refs/heads/{BRANCH}")
    except gitops.GitError:
        return False
    return bool(output.strip())


def _ensure_committer(canonical: Path) -> None:
    """Canonical commits need an identity; hosts may have no global git identity."""
    for key, value in zip(("user.name", "user.email"), COMMITTER, strict=True):
        try:
            gitops.git(canonical, "config", key)
        except gitops.GitError:
            gitops.git(canonical, "config", key, value)


def _clone(canonical: Path, remote: str, result: BootstrapResult) -> None:
    """Fetch an existing workspace into ``canonical`` (which may already hold runtime files)."""
    canonical.mkdir(parents=True, exist_ok=True)
    gitops.git(canonical, "init", "-b", BRANCH)
    gitops.git(canonical, "remote", "add", REMOTE, remote)
    gitops.git(canonical, "fetch", REMOTE)
    gitops.git(canonical, "checkout", "-B", BRANCH, f"{REMOTE}/{BRANCH}")
    gitops.git(canonical, "branch", f"--set-upstream-to={REMOTE}/{BRANCH}", BRANCH)
    _ensure_committer(canonical)
    result.actions.append(f"cloned canonical from {remote}")


def _agents_md(repo_root: Path) -> str:
    doctrine = repo_root / DOCTRINE_FILE
    return doctrine.read_text(encoding="utf-8") if doctrine.exists() else AGENTS_PLACEHOLDER


def _seed(canonical: Path, game: str, remote: str, repo_root: Path, result: BootstrapResult):
    seed = repo_root / "games" / game / "seed"
    if not seed.is_dir():
        raise BootstrapError(f"no seed for game {game!r} at {seed}")
    canonical.mkdir(parents=True, exist_ok=True)
    gitops.git(canonical, "init", "-b", BRANCH)
    _ensure_committer(canonical)
    shutil.copytree(seed, canonical, dirs_exist_ok=True)
    write_text(canonical / "AGENTS.md", _agents_md(repo_root))
    write_text(canonical / ".gitignore", "".join(f"{line}\n" for line in GITIGNORE_LINES))
    (ledger_of(canonical) / ".gitkeep").parent.mkdir(parents=True, exist_ok=True)
    (ledger_of(canonical) / ".gitkeep").touch()
    gitops.git(canonical, "remote", "add", REMOTE, remote)
    gitops.commit_all(canonical, f"bootstrap: seed workspace from games/{game}/seed")
    result.actions.append(f"seeded canonical from games/{game}/seed")
    if not gitops.push(canonical, REMOTE, [f"HEAD:refs/heads/{BRANCH}"]):
        result.warnings.append(f"initial push to {remote} failed; queue-keeper retries it")


def _update_existing(canonical: Path, remote: str | None, result: BootstrapResult) -> None:
    path = canonical / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in lines]
    if missing:
        write_text(path, "\n".join([*lines, *missing]) + "\n")
        with canonical_lock(canonical):
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
    if (canonical / ".git").exists():
        _update_existing(canonical, remote, result)
        return
    if not remote:
        raise BootstrapError(f"{REMOTE_ENV} is not set: it is required to create the workspace")
    if _remote_has_main(remote):
        _clone(canonical, remote, result)
    else:
        _seed(canonical, env.get(GAME_ENV) or DEFAULT_GAME, remote, repo_root, result)


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
        for argv in config_migrations.apply(runner, home):
            result.actions.append("hermes " + " ".join(argv))
        _write_shim(home, result)
        env_values = {
            "AUTORESEARCH_CLI": str(shim_path(home)),
            "HERMES_WRITE_SAFE_ROOT": str(ctx.canonical),
        }
        if set_env_lines(home / ".env", env_values):
            result.actions.append("updated the profile .env")
        _ensure_workspace(ctx, env, Path(repo_root), result)
        _ensure_board(ctx, queue, result)
        if result.board_created:
            _push(ctx.canonical, result)
    except Exception as error:  # noqa: BLE001 - any failure stops the run with a clear error
        result.ok = False
        result.error = f"{type(error).__name__}: {error}"
    return result
