"""Committed bookkeeping under ``canonical/.supervisor/ledger`` (Overview C2).

Every ledger write runs under ``canonical/.supervisor/runtime/canonical.lock`` and is committed
to canonical git with only the ledger paths it touched. Every canonical commit the runtime
makes is recorded in the provenance journal (``runtime.provenance``), which queue-keeper
checks on each run. The lock is not re-entrant: callers of
the functions in this package must not already hold it.
"""

import datetime
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.locks import file_lock
from autoresearch.runtime import provenance

LOCK_NAME = "canonical.lock"


def ledger_of(canonical: Path) -> Path:
    return Path(canonical) / ".supervisor" / "ledger"


def canonical_of(ledger: Path) -> Path:
    """The canonical workspace that owns ``ledger`` (``<canonical>/.supervisor/ledger``)."""
    return Path(ledger).parent.parent


@contextmanager
def canonical_lock(canonical: Path) -> Iterator[None]:
    with file_lock(Path(canonical) / ".supervisor" / "runtime" / LOCK_NAME):
        yield


def commit(canonical: Path, paths: list[Path], message: str) -> str | None:
    """Commit exactly ``paths`` (absolute, under canonical) and record the commit in the
    provenance journal (``runtime.provenance``); call under the canonical lock."""
    relative = [Path(path).relative_to(canonical).as_posix() for path in paths]
    sha = gitops.commit_paths(canonical, relative, message)
    if sha is not None:
        provenance.record(canonical, sha, message)
    return sha


def committed_json(canonical: Path, path: Path) -> dict | None:
    """``path`` (absolute, under canonical) as committed at canonical HEAD, or None if absent.
    The ledger's truth is git: a file written but not committed (a crash before the commit)
    does not count."""
    content = gitops.show(canonical, "HEAD", Path(path).relative_to(canonical).as_posix())
    return None if content is None else json.loads(content)


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
