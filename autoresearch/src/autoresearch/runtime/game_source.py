"""Track the game source commit and fetch the game source for code-citation renewal.

Part of queue-keeper's KB step (System Design §3.5). Only for a game whose contract has
``source: {repo, pin}``:

- ``pin_update`` (outside the canonical lock) reads the current pin (``pin:
  coworld_manifest`` → ``Softmax.game_source_pin``) and compares it with the last one in
  ``.supervisor/ledger/game_source.json`` (``{pin, repo, updated_at}``, committed at canonical
  HEAD). Unchanged, or the platform declares no pin → None. First pin seen → a ``PinUpdate``
  with no diff (only recorded). Changed → the repository is cloned (or fetched) into
  ``$HERMES_HOME/local/game-source/<game>/`` and both commits must be present.
- queue-keeper's ``kb_step`` then, under the lock, runs ``renew_code_citations(old, new)``
  before stale marking and records the new pin in the same commit (``record_pin``). A failure
  before that leaves the old pin, so the next run tries again.
"""

import datetime
from dataclasses import dataclass
from pathlib import Path

from autoresearch.games.loader import Game
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.runctx import RunContext
from autoresearch.runtime.ledger import committed_json, ledger_of
from autoresearch.softmax_tool.protocol import Softmax

LEDGER_FILE = "game_source.json"
CHECKOUT_DIR = Path("local") / "game-source"


@dataclass(frozen=True)
class PinUpdate:
    new: str
    old: str | None  # None: the first pin seen, nothing to diff
    repo: str
    checkout: Path | None  # the fetched repository when there is a diff


def ledger_path(canonical: Path) -> Path:
    return ledger_of(canonical) / LEDGER_FILE


def pin_update(ctx: RunContext, sm: Softmax, game: Game) -> PinUpdate | None:
    if game.source is None:
        return None
    new = sm.game_source_pin(game.league_id)  # the only pin source: coworld_manifest
    if not new:
        return None
    last = committed_json(ctx.canonical, ledger_path(ctx.canonical)) or {}
    old = last.get("pin")
    repo = game.source["repo"]
    if old == new:
        return None
    if old is None:
        return PinUpdate(new, None, repo, None)
    checkout = ctx.hermes_home / CHECKOUT_DIR / game.name
    sync_repo(repo, checkout, [old, new])
    return PinUpdate(new, old, repo, checkout)


def sync_repo(repo: str, checkout: Path, commits: list[str]) -> None:
    """Clone ``repo`` into ``checkout`` (or fetch it) so every commit in ``commits`` exists."""
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        gitops.git(checkout.parent, "clone", "--quiet", repo, str(checkout))
    else:
        gitops.git(
            checkout, "fetch", "--quiet", "--tags", repo, "+refs/heads/*:refs/remotes/origin/*"
        )
    for sha in commits:
        if not _has_commit(checkout, sha):
            gitops.git(checkout, "fetch", "--quiet", repo, sha)  # servers that allow it
        if not _has_commit(checkout, sha):
            raise RuntimeError(f"game source {repo} has no commit {sha}")


def _has_commit(checkout: Path, sha: str) -> bool:
    try:
        gitops.git(checkout, "cat-file", "-e", f"{sha}^{{commit}}")
    except gitops.GitError:
        return False
    return True


def record_pin(canonical: Path, update: PinUpdate, now: datetime.datetime) -> Path:
    """Write the ledger file (the caller commits it, under the lock)."""
    path = ledger_path(canonical)
    write_json(path, {"pin": update.new, "repo": update.repo, "updated_at": now.isoformat()})
    return path
