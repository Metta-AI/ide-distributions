"""The canonical workspace and the one way research reaches it.

Every profile on a host (the orchestrator and each ``researcher-<i>``) works on one game
through one bare git repository, ``RESEARCH_CANONICAL`` (``<root>/canonical-<game>.git``).
Each profile has its own clone, ``RESEARCH_WORKSPACE``, whose ``main`` is checked out and
never edited by hand; a researcher works each card in a Hermes worktree of that clone on a
branch ``card/<id>``.

``integrate`` is the deterministic last step of every card: it commits the worktree, pushes
the card branch first (so recovery is branch-based whatever happens next), validates the
changed records, refuses an overlap with what ``main`` changed since the card's base, merges
under ``canonical.lock`` (a lock file in the bare repository, shared by every profile on the
host), regenerates the knowledge-base views and pushes ``main``. "Integrated" is a git fact:
the card branch's commit is an ancestor of canonical ``main`` (``merged``).

``publish`` is the orchestrator's path for the files only it writes (``plan.md``,
``control/``), and ``bootstrap`` creates or clones the workspace of a fresh profile.
"""

import json
import os
import re
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from ide_research_core.io import gitops
from ide_research_core.io.docs import write_text
from ide_research_core.io.locks import file_lock
from ide_research_core.kb.index import write_generated
from ide_research_core.paths import Layout
from ide_research_core.records.registry import REGISTRY, Doc, normalize, parse_record_path
from ide_research_core.records.store import Change, RecordStore

WORKSPACE_ENV = "RESEARCH_WORKSPACE"
CANONICAL_ENV = "RESEARCH_CANONICAL"
GAME_ENV = "RESEARCH_GAME"
DEFAULT_GAME = "gota"
REMOTE = "origin"
MAIN = "main"
LOCK_NAME = "canonical.lock"
LOCK_TIMEOUT_S = 600
COMMITTER = ("research", "research@localhost")
GITIGNORE_LINES = (".supervisor/runtime/", ".worktrees/", ".scratch/")

# Only the orchestrator changes these (design: permissions); integrate refuses them from a card.
PROTECTED_PREFIXES = ("strategy/STRATEGY.md", "control/", "promotions/")
# Integrate writes these after the merge, so a card that edits them would only conflict.
GENERATED_PREFIXES = ("kb/index.md", "kb/_generated/")

DISTRIBUTION_ROOT = Path(__file__).resolve().parents[2]  # games/ and doctrine/ live here
DOCTRINE_FILES = ("research-agent.md", "communication.md")
VERSION_HEADER = re.compile(r"\A<!-- doctrine-version: (\S+) -->")

_STORE = RecordStore(Layout(Path()))


class WorkspaceError(RuntimeError):
    """A refusal with a stable ``reason``: validate, overlap, merge, push, git or setup."""

    def __init__(self, reason: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}


@dataclass(frozen=True)
class Integrated:
    card_sha: str
    main_sha: str
    already_merged: bool

    def to_dict(self) -> dict:
        return {
            "card_sha": self.card_sha,
            "main_sha": self.main_sha,
            "already_merged": self.already_merged,
        }


def workspace_from_env(env: Mapping[str, str] = os.environ) -> Path:
    if env.get(WORKSPACE_ENV):
        return Path(env[WORKSPACE_ENV])
    return Path(env["HERMES_HOME"]) / "workspace"


def canonical_of(clone: Path) -> Path:
    """The bare repository a clone pushes to (its ``origin``)."""
    return Path(gitops.git(clone, "remote", "get-url", REMOTE))


@contextmanager
def canonical_lock(bare: Path) -> Iterator[None]:
    """Serialize every write to canonical ``main`` on this host."""
    with file_lock(Path(bare) / LOCK_NAME, timeout_s=LOCK_TIMEOUT_S):
        yield


def _is_ancestor(repo: Path, ancestor: str, sha: str) -> bool:
    try:
        gitops.git(repo, "merge-base", "--is-ancestor", ancestor, sha)
    except gitops.GitError:
        return False
    return True


def _fast_forward_main(clone: Path) -> str:
    """Bring the clone's checked-out ``main`` to canonical ``main``; return its sha."""
    gitops.git(clone, "fetch", "-q", REMOTE, MAIN)
    gitops.git(clone, "merge", "-q", "--ff-only", f"{REMOTE}/{MAIN}")
    return gitops.head(clone)


# --- validation ---


def _doc_at(repo: Path, sha: str, path: str, entity: str) -> Doc | None:
    content = gitops.show(repo, sha, path)
    if content is None:
        return None
    text = content.decode("utf-8")
    if REGISTRY[entity].fmt == "json":
        return normalize(json.loads(text)), ""
    post = frontmatter.loads(text)
    return normalize(dict(post.metadata)), post.content


def validate(repo: Path, base: str, result: str) -> tuple[list[str], set[str]]:
    """(problems, ids of existing records the change touches) for ``base..result``.

    Problems: a protected or generated path changed, a record file that cannot be parsed,
    and every record change the mutability rules refuse (append-only fields, frozen
    designs, close-don't-delete; ``RecordStore.validate_changes``)."""
    problems: list[str] = []
    changes: list[Change] = []
    for status, path in gitops.diff_status(repo, base, result):
        if path.startswith(PROTECTED_PREFIXES):
            problems.append(f"{path}: only the orchestrator changes this path")
            continue
        if path.startswith(GENERATED_PREFIXES):
            problems.append(f"{path}: integrate writes the generated views; do not edit them")
            continue
        parsed = parse_record_path(_STORE.layout, path)
        if parsed is None:
            continue
        try:
            before = None if status == "A" else _doc_at(repo, base, path, parsed[0])
            after = None if status == "D" else _doc_at(repo, result, path, parsed[0])
        except (ValueError, UnicodeDecodeError) as error:
            problems.append(f"{path} cannot be parsed: {error}")
            continue
        changes.append((path, before, after))
    for found in _STORE.validate_changes(changes).values():
        problems += found
    existing = {
        parse_record_path(_STORE.layout, path)[1] for path, before, _ in changes if before
    }
    return problems, existing


def _changed_ids(repo: Path, since: str, until: str) -> set[str]:
    ids = set()
    for _, path in gitops.diff_status(repo, since, until):
        parsed = parse_record_path(_STORE.layout, path)
        if parsed is not None:
            ids.add(parsed[1])
    return ids


# --- integrate ---


def _commit_worktree(worktree: Path, branch: str) -> str:
    gitops.git(worktree, "add", "-A")
    if gitops.git(worktree, "status", "--porcelain"):
        gitops.git(worktree, "commit", "-q", "-m", f"{branch}: result")
    return gitops.head(worktree)


def _current_branch(worktree: Path) -> str:
    branch = gitops.git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise WorkspaceError("git", f"{worktree} is not on a branch")
    return branch


def integrate(clone: Path, *, worktree: Path | None = None, branch: str | None = None) -> Integrated:
    """Integrate a card into canonical ``main``.

    A researcher passes its ``worktree`` (the branch is the worktree's); the orchestrator
    passes ``branch`` to integrate a card branch that is already pushed. Raises
    ``WorkspaceError`` with reason ``validate``, ``overlap``, ``merge`` or ``push``; the card
    branch is pushed before any of those can happen."""
    if (worktree is None) == (branch is None):
        raise WorkspaceError("setup", "pass exactly one of a worktree or a branch")
    if worktree is not None:
        branch = _current_branch(worktree)
        result = _commit_worktree(worktree, branch)
        pushed, rejected = gitops.push_with_rejections(worktree, REMOTE, [f"{branch}:{branch}"])
        if not pushed:
            raise WorkspaceError("push", f"push of {branch} was rejected", {"rejected": rejected})
    gitops.git(clone, "fetch", "-q", REMOTE, f"+refs/heads/{branch}:refs/remotes/{REMOTE}/{branch}")
    result = gitops.git(clone, "rev-parse", f"{REMOTE}/{branch}")
    with canonical_lock(canonical_of(clone)):
        main = _fast_forward_main(clone)
        if _is_ancestor(clone, result, main):
            return Integrated(result, main, already_merged=True)
        base = gitops.merge_base(clone, result, main)
        problems, existing = validate(clone, base, result)
        if problems:
            raise WorkspaceError("validate", "; ".join(problems), {"problems": problems})
        overlap = sorted(existing & _changed_ids(clone, base, main))
        if overlap:
            raise WorkspaceError(
                "overlap",
                f"{branch} changes records that main also changed since {base[:12]}: "
                + ", ".join(overlap),
                {"overlap": overlap, "base": base},
            )
        merge = gitops._run(clone, "merge", "--no-ff", "-q", "-m", f"merge {branch}", result, check=False)
        if merge.returncode != 0:
            conflicts = sorted(gitops.git(clone, "diff", "--name-only", "--diff-filter=U").splitlines())
            gitops._run(clone, "merge", "--abort", check=False)
            raise WorkspaceError(
                "merge", f"{branch} conflicts with main in: " + ", ".join(conflicts),
                {"conflicts": conflicts},
            )
        _regenerate_views(clone)
        _push_main(clone)
        return Integrated(result, gitops.head(clone), already_merged=False)


def _regenerate_views(clone: Path) -> None:
    written = write_generated(Layout(clone))
    paths = [path.relative_to(clone).as_posix() for path in written]
    if gitops.git(clone, "status", "--porcelain", "--", *paths):
        gitops.git(clone, "add", "--", *paths)
        gitops.git(clone, "commit", "-q", "-m", "kb: generated views")


def _push_main(clone: Path) -> None:
    pushed, rejected = gitops.push_with_rejections(clone, REMOTE, [f"{MAIN}:{MAIN}"])
    if not pushed:
        gitops.git(clone, "reset", "-q", "--hard", f"{REMOTE}/{MAIN}")
        raise WorkspaceError("push", "push of main was rejected", {"rejected": rejected})


def merged(clone: Path, branch: str) -> dict:
    """Whether ``branch``'s commit is an ancestor of canonical ``main``."""
    gitops.git(clone, "fetch", "-q", REMOTE, MAIN)
    main = gitops.git(clone, "rev-parse", f"{REMOTE}/{MAIN}")
    try:
        gitops.git(clone, "fetch", "-q", REMOTE, f"+refs/heads/{branch}:refs/remotes/{REMOTE}/{branch}")
    except gitops.GitError:
        return {"merged": False, "card_sha": None, "main_sha": main}
    card = gitops.git(clone, "rev-parse", f"{REMOTE}/{branch}")
    return {"merged": _is_ancestor(clone, card, main), "card_sha": card, "main_sha": main}


def publish(clone: Path, paths: list[str], message: str) -> dict:
    """Commit exactly ``paths`` (already written in the clone) and push ``main``."""
    with canonical_lock(canonical_of(clone)):
        _fast_forward_main(clone)
        gitops.git(clone, "add", "--", *paths)
        if gitops.git(clone, "diff", "--cached", "--name-only", "--", *paths):
            gitops.git(clone, "commit", "-q", "-m", message, "--", *paths)
            _push_main(clone)
        return {"main_sha": gitops.head(clone)}


# --- bootstrap ---


def _ensure_committer(repo: Path) -> None:
    """Canonical commits need an identity; hosts may have no global git identity."""
    for key, value in zip(("user.name", "user.email"), COMMITTER, strict=True):
        try:
            gitops.git(repo, "config", key)
        except gitops.GitError:
            gitops.git(repo, "config", key, value)


def _doctrine_version(root: Path) -> str | None:
    path = root / "doctrine" / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def agents_md(root: Path) -> str | None:
    """The workspace ``AGENTS.md``: a version header, then the doctrine files. Hermes loads
    it as a context file in every card worktree."""
    version = _doctrine_version(root)
    if version is None:
        return None
    header = (
        f"<!-- doctrine-version: {version} -->\n"
        "# Research doctrine\n\n"
        f'Doctrine version {version}. Run the research CLI as `"$RESEARCH_CLI"`.\n\n'
    )
    parts = [(root / "doctrine" / name).read_text(encoding="utf-8") for name in DOCTRINE_FILES]
    return header + "\n".join(parts)


def _seed(clone: Path, game: str, root: Path) -> None:
    seed = root / "games" / game / "seed"
    if not seed.is_dir():
        raise WorkspaceError("setup", f"no seed for game {game!r} at {seed}")
    shutil.copytree(seed, clone, dirs_exist_ok=True)
    write_text(clone / ".gitignore", "".join(f"{line}\n" for line in GITIGNORE_LINES))
    doctrine = agents_md(root)
    if doctrine is not None:
        write_text(clone / "AGENTS.md", doctrine)
    gitops.git(clone, "add", "-A")
    gitops.git(clone, "commit", "-q", "-m", f"bootstrap: seed workspace from games/{game}/seed")


def _reseed_agents_md(clone: Path, root: Path) -> bool:
    """Rewrite ``AGENTS.md`` when the shipped doctrine version differs from canonical's."""
    wanted = _doctrine_version(root)
    path = clone / "AGENTS.md"
    current = VERSION_HEADER.match(path.read_text(encoding="utf-8")) if path.exists() else None
    if wanted is None or (current and current.group(1) == wanted):
        return False
    write_text(path, agents_md(root))
    publish(clone, ["AGENTS.md"], f"bootstrap: doctrine version {wanted}")
    return True


def bootstrap(
    clone: Path, bare: Path, game: str = DEFAULT_GAME, root: Path = DISTRIBUTION_ROOT
) -> list[str]:
    """Make ``bare`` and this profile's ``clone`` exist and agree; return what was done.

    Idempotent. The first profile on a host creates the bare repository and seeds it from
    ``games/<game>/seed``; every later profile clones it."""
    actions = []
    if not (bare / "HEAD").exists():
        bare.mkdir(parents=True, exist_ok=True)
        gitops.git(bare, "init", "-q", "--bare", "-b", MAIN)
        actions.append(f"created {bare}")
    if not (clone / ".git").exists():
        clone.mkdir(parents=True, exist_ok=True)
        gitops.git(clone, "init", "-q", "-b", MAIN)
        gitops.git(clone, "remote", "add", REMOTE, str(bare))
        _ensure_committer(clone)
        with canonical_lock(bare):
            if gitops.git(bare, "for-each-ref", f"refs/heads/{MAIN}"):
                gitops.git(clone, "fetch", "-q", REMOTE, MAIN)
                gitops.git(clone, "checkout", "-q", "-B", MAIN, f"{REMOTE}/{MAIN}")
                actions.append(f"cloned {bare}")
            else:
                _seed(clone, game, root)
                gitops.git(clone, "push", "-q", REMOTE, f"{MAIN}:{MAIN}")
                actions.append(f"seeded {bare} from games/{game}/seed")
        gitops.git(clone, "branch", "-q", f"--set-upstream-to={REMOTE}/{MAIN}", MAIN)
    if _reseed_agents_md(clone, root):
        actions.append("re-seeded AGENTS.md")
    return actions


SHIM_NAME = "research"


def write_cli_shim(home: Path) -> Path:
    """``$HERMES_HOME/bin/research``: the research CLI from the profile venv."""
    python = home / "local" / "venv" / "bin" / "python"
    path = home / "bin" / SHIM_NAME
    wanted = f'#!/bin/sh\nexec "{python}" -m ide_research_core "$@"\n'
    if not path.exists() or path.read_text() != wanted:
        write_text(path, wanted)
    path.chmod(0o755)
    return path
