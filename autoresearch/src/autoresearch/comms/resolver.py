"""Resolve ``ar://`` references against a commit of a workspace repo (never its working tree).

Minimal resolver (Task 3.2): core record types ``ar://<type>/<id>`` for the types in
``CORE_TYPES``, ``ar://plan``, ``ar://file/<path>`` and ``ar://handoff/<task>/<run>``. A
``#fragment`` is ignored here and the whole document is returned. Task 3.5 extends this with
anchors, archived claims, and game-declared reference types.
"""

from dataclasses import dataclass
from pathlib import Path

from autoresearch.io import gitops
from autoresearch.paths import Layout
from autoresearch.records.registry import REGISTRY, parse_record_path

SCHEME = "ar://"
CORE_TYPES = (
    "topic",
    "hypothesis",
    "experiment",
    "result",
    "version",
    "claim",
    "hub",
    "lesson",
    "journal",
    "evidence",
)
HANDOFFS_DIR = ".supervisor/ledger/handoffs"

# Layout with an empty root: its properties are paths relative to the repository root.
_RELATIVE = Layout(Path())


class ResolveError(LookupError):
    """The reference is malformed, of an unknown type, or absent at the requested revision."""


@dataclass(frozen=True)
class Resolved:
    path: str  # repo-relative path of the file the reference names
    content: bytes
    rev: str  # full commit sha the content was read at


def _record_path(repo: Path, sha: str, entity_name: str, id_: str) -> str:
    directory = getattr(_RELATIVE, REGISTRY[entity_name].subdir).as_posix()
    listing = gitops.git(repo, "ls-tree", "-r", "--name-only", sha, "--", f"{directory}/")
    for path in listing.splitlines():
        if parse_record_path(_RELATIVE, path) == (entity_name, id_):
            return path
    raise ResolveError(f"no {entity_name} {id_} at {sha[:12]}")


def _path_for(repo: Path, sha: str, uri: str, kind: str, rest: str) -> str:
    if kind == "plan" and not rest:
        return _RELATIVE.plan.as_posix()
    if kind == "file" and rest:
        return rest
    if kind == "handoff" and rest.count("/") == 1 and all(rest.split("/")):
        return f"{HANDOFFS_DIR}/{rest}.json"
    if kind in CORE_TYPES and rest and "/" not in rest:
        return _record_path(repo, sha, kind, rest)
    raise ResolveError(f"unknown or malformed reference {uri!r}")


def resolve(canonical: Path, uri: str, rev: str | None = None) -> Resolved:
    if not uri.startswith(SCHEME):
        raise ResolveError(f"not an ar:// reference: {uri!r}")
    target = uri.removeprefix(SCHEME).split("#", 1)[0]
    kind, _, rest = target.partition("/")
    try:
        sha = gitops.git(canonical, "rev-parse", "--verify", f"{rev or 'HEAD'}^{{commit}}")
    except gitops.GitError as error:
        raise ResolveError(f"unknown revision {rev or 'HEAD'!r}") from error
    path = _path_for(canonical, sha, uri, kind, rest)
    content = gitops.show(canonical, sha, path)
    if content is None:
        raise ResolveError(f"{uri!r}: {path} does not exist at {sha[:12]}")
    return Resolved(path=path, content=content, rev=sha)
