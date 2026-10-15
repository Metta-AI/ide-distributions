"""Bootstrap: the first step of every queue-keeper run (Overview C1, amendments A2, A6, A7).

Local setup only, idempotent, and it never creates cards (admission does, after preflight):

1. The venv is the launcher's job (``ide_research_core.launch``), already done when this runs.
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
   the directory when a coach clicks a research button early). Seed commits only the paths
   it wrote, so those files stay untracked; clone holds them next to the workspace
   directory during checkout and puts them back (a held file a crash left behind is put
   back on the next run). queue-keeper's routing then adopts them like any direct write. Bootstrap's own canonical
   commits are recorded in the provenance journal, and when the journal has no anchor yet it
   trusts the current HEAD once (``provenance.initialize``: a new or restored workspace, or the
   first run of the provenance check on an existing one). A journal or key that cannot be read
   does not fail bootstrap, neither in ``initialize`` nor in another bootstrap commit
   (``.gitignore``, ``AGENTS.md``, the board epoch; ``BootstrapResult.provenance_error``):
   queue-keeper treats it as a provenance check that cannot run, and pauses.
5. The board (A2): ``queue.ensure_board()`` when ``queue.board_exists()`` is False, and then a
   new board epoch in ``ledger/board.json`` (committed).

A failure is returned in ``BootstrapResult.error``; queue-keeper then stops with exit 1.
"""

import re
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ide_research_core.io import gitops
from ide_research_core.io.docs import write_text
from ide_research_core.paths import Layout
from ide_research_core.queue.base import Queue
from ide_research_core.runctx import RunContext
from ide_research_core.runtime import config_migrations, provenance
from ide_research_core.runtime.config_migrations import Runner
from ide_research_core.runtime.ledger import canonical_lock, ledger_of
from ide_research_core.runtime.rebuild import new_board_epoch

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
    # The provenance journal could not be read or initialized; queue-keeper fails closed on
    # it (provenance pause, spend caps) instead of failing bootstrap.
    provenance_error: str | None = None
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


def _holding_dirs(canonical: Path) -> tuple[Path, Path]:
    """Where clone keeps early inbox files, and where clashing ones end up: fixed names next
    to ``canonical`` (same filesystem, so a move is a rename; a fixed name lets the next run
    find what a crash left behind)."""
    return (
        canonical.parent / f".{canonical.name}-early-inbox",
        canonical.parent / f".{canonical.name}-early-inbox-clashed",
    )


def _restore_early_inbox(canonical: Path, result: BootstrapResult) -> None:
    """Move held early inbox files back into ``comms/inbox/`` (names and mtimes kept),
    untracked, for routing to adopt. A name a clone already tracks is never overwritten:
    that file moves to the clashed directory, with one warning."""
    holding, clashed = _holding_dirs(canonical)
    if not holding.is_dir():
        return
    inbox = canonical / INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    kept, clashes = 0, []
    for entry in sorted(holding.iterdir()):
        if (inbox / entry.name).exists():
            clashed.mkdir(exist_ok=True)
            if not (clashed / entry.name).exists():
                shutil.move(entry, clashed / entry.name)
                clashes.append(entry.name)
        else:
            shutil.move(entry, inbox / entry.name)
            kept += 1
    if kept:
        result.actions.append(f"kept {kept} inbox files written before bootstrap for adoption")
    if clashes:
        result.warnings.append(
            f"inbox files {clashes} written before bootstrap clash with tracked files; "
            f"moved to {clashed}"
        )
    if not any(holding.iterdir()):
        holding.rmdir()


@contextmanager
def _early_inbox_set_aside(canonical: Path, result: BootstrapResult) -> Iterator[None]:
    """Hold what an external agent wrote to ``comms/inbox/`` before the first bootstrap
    outside ``canonical`` while it is cloned (``git checkout`` refuses to overwrite an
    untracked file the remote tracks), then put it back (``_restore_early_inbox``).
    Dotfiles stay: the bridge writes ``.<id>.json.tmp`` and then renames it, and adoption
    never commits a dotfile, so no checkout can collide with one. A restore that fails is a
    warning, never masking the clone's own error; the next bootstrap retries it."""
    inbox = canonical / INBOX
    entries = [e for e in sorted(inbox.iterdir()) if not e.name.startswith(".")] if inbox.is_dir() else []
    holding, _ = _holding_dirs(canonical)
    if entries:
        holding.mkdir(exist_ok=True)
        for entry in entries:
            shutil.move(entry, holding / entry.name)
    try:
        yield
    finally:
        try:
            _restore_early_inbox(canonical, result)
        except OSError as error:
            result.warnings.append(
                f"could not put back inbox files from {holding}: {error}; the next run retries"
            )


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


@contextmanager
def _journal_failures(result: BootstrapResult) -> Iterator[None]:
    """A bootstrap commit that fails because the provenance journal cannot be read or
    written does not fail bootstrap: queue-keeper must still reach its fail-closed pause and
    the spend caps (``BootstrapResult.provenance_error``)."""
    try:
        yield
    except provenance.JournalError as error:
        _provenance_failed(result, error)


def _provenance_failed(result: BootstrapResult, error: Exception) -> None:
    message = f"{type(error).__name__}: {error}"
    result.provenance_error = result.provenance_error or message
    result.warnings.append(f"provenance journal: {message}")


def _reseed_agents_md(canonical: Path, repo_root: Path, result: BootstrapResult) -> None:
    """Rewrite ``AGENTS.md`` when ``doctrine/VERSION`` differs from the header's version."""
    wanted = _doctrine_version(repo_root)
    if wanted is None:
        return
    path = canonical / "AGENTS.md"
    current = VERSION_HEADER.match(path.read_text(encoding="utf-8")) if path.exists() else None
    if current and current.group(1) == wanted:
        return
    with _journal_failures(result), canonical_lock(canonical):
        write_text(path, _agents_md(repo_root))
        subject = f"bootstrap: doctrine version {wanted}"
        provenance.commit_recorded(
            canonical, subject, lambda: gitops.prepare_paths(canonical, ["AGENTS.md"], subject)
        )
        result.actions.append(f"re-seeded AGENTS.md at doctrine version {wanted}")


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
    gitkeep = ledger_of(canonical) / ".gitkeep"
    gitkeep.parent.mkdir(parents=True, exist_ok=True)
    gitkeep.touch()
    gitops.git(canonical, "remote", "add", REMOTE, remote)
    # Exactly what seeding wrote: anything else already in the directory (an inbox file an
    # external agent wrote before this first bootstrap) stays untracked, for routing to adopt.
    paths = [p.relative_to(seed).as_posix() for p in sorted(seed.rglob("*")) if p.is_file()]
    paths += ["AGENTS.md", ".gitignore", gitkeep.relative_to(canonical).as_posix()]
    subject = f"bootstrap: seed workspace from games/{game}/seed"
    provenance.commit_recorded(canonical, subject, lambda: gitops.prepare_paths(canonical, paths, subject))
    early = sorted((canonical / INBOX).iterdir()) if (canonical / INBOX).is_dir() else []
    if early:
        result.actions.append(f"kept {len(early)} inbox files written before bootstrap for adoption")
    result.actions.append(f"seeded canonical from games/{game}/seed")
    with canonical_lock(canonical):
        pushed = provenance.push_main(canonical, REMOTE)
    if not pushed:
        result.warnings.append(f"initial push to {remote} failed; queue-keeper retries it")


def _update_existing(canonical: Path, remote: str | None, result: BootstrapResult) -> None:
    path = canonical / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in lines]
    if missing:
        with _journal_failures(result), canonical_lock(canonical):
            write_text(path, "\n".join([*lines, *missing]) + "\n")
            subject = "bootstrap: .gitignore runtime dirs"
            provenance.commit_recorded(
                canonical, subject, lambda: gitops.prepare_paths(canonical, [".gitignore"], subject)
            )
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
) -> bool:
    """Return True when this run created the workspace (cloned or seeded)."""
    canonical = ctx.canonical
    _restore_early_inbox(canonical, result)  # what a clone that crashed left held
    remote = env.get(REMOTE_ENV) or None
    if remote:
        remote = resolve_remote(remote, ctx.hermes_home)
    if (canonical / ".git").exists():
        _update_existing(canonical, remote, result)
        _reseed_agents_md(canonical, repo_root, result)
        return False
    if not remote:
        raise BootstrapError(f"{REMOTE_ENV} is not set: it is required to create the workspace")
    if _remote_has_main(remote, ctx.hermes_home):
        _clone(canonical, remote, result)
        _reseed_agents_md(canonical, repo_root, result)
    else:
        _seed(canonical, env.get(GAME_ENV) or DEFAULT_GAME, remote, repo_root, result)
    return True


def _ensure_provenance(canonical: Path, fresh: bool, result: BootstrapResult) -> None:
    """Initialize the journal. A failure (an unreadable journal or key) does not fail
    bootstrap: queue-keeper must still reach its fail-closed provenance pause."""
    try:
        with canonical_lock(canonical):
            action = provenance.initialize(canonical, fresh)
    except Exception as error:  # noqa: BLE001 - reported as the provenance step failure
        _provenance_failed(result, error)
        return
    if action:
        result.actions.append(action)


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
    result.board_created = True
    with _journal_failures(result), canonical_lock(ctx.canonical):
        epoch = new_board_epoch(ctx.canonical, "board created by bootstrap")
        result.actions.append(f"created the board (epoch {epoch})")


def _push(canonical: Path, result: BootstrapResult) -> None:
    """Push the epoch commit now (queue-keeper's push step would too, but ``--bootstrap-only``
    stops before it)."""
    if REMOTE not in gitops.git(canonical, "remote").split():
        return
    with canonical_lock(canonical):
        pushed = provenance.push_main(canonical, REMOTE)
    if not pushed:
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
        fresh = _ensure_workspace(ctx, env, Path(repo_root), result)
        _ensure_provenance(ctx.canonical, fresh, result)
        _ensure_comms_dirs(ctx.canonical)
        _ensure_board(ctx, queue, result)
        if result.board_created:
            _push(ctx.canonical, result)
    except Exception as error:  # noqa: BLE001 - any failure stops the run with a clear error
        result.ok = False
        result.error = f"{type(error).__name__}: {error}"
    return result
