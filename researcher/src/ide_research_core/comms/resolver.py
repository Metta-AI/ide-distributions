"""Resolve ``ar://`` references against a commit of a workspace repo (never its working tree).

``resolve(canonical, uri, rev)`` reads with ``git show <rev>:<path>``; ``rev`` defaults to
HEAD and the result carries the full commit SHA it was read at. Reference forms
(System Design 7):

- ``ar://<record type>/<id>``: topic, hypothesis, experiment, result, version, claim, hub,
  lesson, journal, evidence. Claims are also found in ``kb/archive/`` (the primary directory
  wins if a claim is in both).
- ``ar://wiki`` is ``kb/index.md``; ``ar://wiki/<C-id|HUB-id>`` is that claim or hub.
- ``ar://plan`` is ``plan.md``; ``ar://file/<path>`` is any committed file.
- ``ar://xreq/<platform id>`` and ``ar://episode/<platform id>``: the evidence index record
  (``evidence/index/<EV>.json``) whose ``source_ids`` holds that id as a value or in a list
  value, under any key; the lowest EV id wins if several do.
- ``ar://opponent/<opponent id>``: the opponent's hub (a hub whose front matter has
  ``opponent: <id>``); else a claim with ``kind: opponent`` and ``opponent: <id>``; else the
  opponent model ``kb/opponents/<id>/STRATEGY.md``. The lowest record id wins among hubs or
  claims.
- ``ar://skill-proposal/<name>`` is ``skills-proposed/<name>/SKILL.md``;
  ``ar://doctrine-proposal/<name>`` is ``doctrine-proposed/<name>``, else
  ``doctrine-proposed/<name>.md``.
- ``ar://handoff/<task>/<run>``: the handoff record, or for a completed run that left none,
  the runtime's ``orphans/<task>-<run>.json`` marker (fragment ``#missing``).
- A game type (``Game.reference_types``, never shadowing a core type): the game's resolver
  command runs from the autoresearch repo root with the id as its last argument and
  ``AUTORESEARCH_WORKSPACE`` (canonical) and ``AUTORESEARCH_REV`` (the full SHA) in its
  environment. Exit 0 and stdout is the content; any other exit, or no exit within
  ``GAME_RESOLVER_TIMEOUT_S`` (30 s), is "not found". ``path`` is
  then ``<type>/<id>``, since the content need not be a workspace file.

Anchors. ``ar://journal/<J>#dN`` is the Nth top-level list item under the journal body's
``## Decisions`` heading (1-based; continuation lines included, the bullet removed).
``ar://lesson#<L>`` and ``ar://lesson/<L>#<L>`` name the lesson entry ``<L>`` (one lesson
per file). Any other fragment is ignored and the whole document is returned.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import frontmatter
import yaml

from ide_research_core.games.loader import Game, binding_argv
from ide_research_core.io import gitops
from ide_research_core.paths import Layout
from ide_research_core.records.registry import entity_of_id, parse_record_path, record_dirs

SCHEME = "ar://"
RECORD_TYPES = (
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
CORE_TYPES = frozenset(
    {
        *RECORD_TYPES,
        "wiki",
        "plan",
        "xreq",
        "episode",
        "file",
        "handoff",
        "skill-proposal",
        "doctrine-proposal",
        "opponent",
    }
)
HANDOFFS_DIR = ".supervisor/ledger/handoffs"
ORPHANS_DIR = ".supervisor/ledger/orphans"
SKILLS_PROPOSED_DIR = "skills-proposed"
DOCTRINE_PROPOSED_DIR = "doctrine-proposed"
OPPONENT_MODELS_DIR = "kb/opponents"
WIKI_INDEX = "kb/index.md"
DECISIONS_HEADING = "## Decisions"
GAME_ENV_WORKSPACE = "AUTORESEARCH_WORKSPACE"
GAME_ENV_REV = "AUTORESEARCH_REV"
GAME_RESOLVER_TIMEOUT_S = 30
# Game resolver commands are relative to the autoresearch repo root (where games/ lives).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DECISION_ANCHOR = re.compile(r"d(?P<n>[1-9][0-9]*)")
_LESSON_ID = re.compile(r"L-[0-9A-HJKMNP-TV-Z]{26}")
_LIST_ITEM = re.compile(r"(?:[-*+]|[0-9]+[.)])\s+(?P<text>.*)")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Layout with an empty root: its properties are paths relative to the repository root.
_RELATIVE = Layout(Path())


class ResolveError(LookupError):
    """The reference is malformed, of an unknown type, or absent at the requested revision."""


@dataclass(frozen=True)
class Resolved:
    path: str  # repo-relative path of the file the reference names (game types: <type>/<id>)
    content: bytes
    rev: str  # full commit sha the content was read at


# --- lookups at a commit ---


def _record_path(repo: Path, sha: str, entity_name: str, id_: str) -> str:
    # Search the primary directory before the archive, as path_for does.
    for directory in record_dirs(_RELATIVE, entity_name):
        listing = gitops.git(
            repo, "ls-tree", "-r", "--name-only", sha, "--", f"{directory.as_posix()}/"
        )
        for path in listing.splitlines():
            if parse_record_path(_RELATIVE, path) == (entity_name, id_):
                return path
    raise ResolveError(f"no {entity_name} {id_} at {sha[:12]}")


def _files_containing(repo: Path, sha: str, text: str, *directories: str) -> list[str]:
    """Paths under ``directories`` at ``sha`` whose content contains ``text``, sorted."""
    result = subprocess.run(
        ["git", "-C", str(repo), "grep", "-l", "-F", "-e", text, sha, "--", *directories],
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):  # 1 means no match
        raise ResolveError(f"git grep failed: {result.stderr.decode(errors='replace').strip()}")
    prefix = f"{sha}:"
    return sorted(line.removeprefix(prefix) for line in result.stdout.decode().splitlines())


def _front_matter(repo: Path, sha: str, path: str) -> dict:
    content = gitops.show(repo, sha, path) or b""
    try:
        return dict(frontmatter.loads(content.decode("utf-8")).metadata)
    except (ValueError, yaml.YAMLError):
        return {}


def _holds(value, id_: str) -> bool:
    return value == id_ or (isinstance(value, list) and id_ in value)


def _evidence_for(repo: Path, sha: str, uri: str, platform_id: str) -> str:
    directory = _RELATIVE.evidence_index.as_posix()
    for path in _files_containing(repo, sha, platform_id, f"{directory}/"):
        try:
            record = json.loads(gitops.show(repo, sha, path) or b"null")
        except ValueError:
            continue
        source_ids = record.get("source_ids") if isinstance(record, dict) else None
        if isinstance(source_ids, dict) and any(
            _holds(v, platform_id) for v in source_ids.values()
        ):
            return path
    raise ResolveError(f"{uri!r}: no evidence record cites {platform_id} at {sha[:12]}")


def _opponent_path(repo: Path, sha: str, opponent_id: str) -> str:
    candidates = _files_containing(
        repo, sha, opponent_id, f"{_RELATIVE.hubs.as_posix()}/", f"{_RELATIVE.claims.as_posix()}/"
    )
    records = [(parse_record_path(_RELATIVE, path), path) for path in candidates]
    for wanted in ("hub", "claim"):
        for parsed, path in sorted((p, path) for p, path in records if p and p[0] == wanted):
            meta = _front_matter(repo, sha, path)
            if meta.get("opponent") == opponent_id and (
                wanted == "hub" or meta.get("kind") == "opponent"
            ):
                return path
    return f"{OPPONENT_MODELS_DIR}/{opponent_id}/STRATEGY.md"


def _single_name(uri: str, rest: str) -> str:
    if not _NAME.fullmatch(rest) or ".." in rest:
        raise ResolveError(f"{uri!r}: expected one name (letters, digits, '.', '_', '-')")
    return rest


def _handoff_path(repo: Path, sha: str, rest: str) -> str:
    path = f"{HANDOFFS_DIR}/{rest}.json"
    if gitops.show(repo, sha, path) is None:
        task_id, run_id = rest.split("/")
        orphan = f"{ORPHANS_DIR}/{task_id}-{run_id}.json"
        return orphan if gitops.show(repo, sha, orphan) is not None else path
    return path


def _wiki_path(repo: Path, sha: str, uri: str, rest: str) -> str:
    if not rest:
        return WIKI_INDEX
    try:
        entity = entity_of_id(rest).name
    except ValueError:
        entity = None
    if entity not in ("claim", "hub"):
        raise ResolveError(f"{uri!r}: a wiki reference names a claim or a hub")
    return _record_path(repo, sha, entity, rest)


def _path_for(repo: Path, sha: str, uri: str, kind: str, rest: str, anchor: str) -> str:
    if kind == "plan" and not rest:
        return _RELATIVE.plan.as_posix()
    if kind == "file" and rest:
        return rest
    if kind == "wiki":
        return _wiki_path(repo, sha, uri, rest)
    if kind == "handoff" and rest.count("/") == 1 and all(rest.split("/")):
        return _handoff_path(repo, sha, rest)
    if kind in ("xreq", "episode") and rest:
        return _evidence_for(repo, sha, uri, _single_name(uri, rest))
    if kind == "opponent" and rest:
        return _opponent_path(repo, sha, _single_name(uri, rest))
    if kind == "skill-proposal" and rest:
        return f"{SKILLS_PROPOSED_DIR}/{_single_name(uri, rest)}/SKILL.md"
    if kind == "doctrine-proposal" and rest:
        exact = f"{DOCTRINE_PROPOSED_DIR}/{_single_name(uri, rest)}"
        return exact if gitops.show(repo, sha, exact) is not None else f"{exact}.md"
    if kind == "lesson" and _LESSON_ID.fullmatch(anchor):
        if rest and rest != anchor:
            raise ResolveError(f"{uri!r}: the lesson anchor names a different lesson")
        return _record_path(repo, sha, "lesson", anchor)
    if kind in RECORD_TYPES and rest and "/" not in rest:
        return _record_path(repo, sha, kind, rest)
    raise ResolveError(f"unknown or malformed reference {uri!r}")


# --- anchors ---


def _decision(content: bytes, number: int, uri: str) -> bytes:
    """The ``number``-th top-level list item under ``## Decisions`` in a journal body."""
    body = frontmatter.loads(content.decode("utf-8")).content
    items: list[list[str]] = []
    in_section = False
    for line in body.splitlines():
        if line.startswith("#"):
            in_section = line.strip() == DECISIONS_HEADING
            continue
        if not in_section:
            continue
        item = _LIST_ITEM.fullmatch(line)
        if item:
            items.append([item["text"]])
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1].append(line.strip())
    if number > len(items):
        raise ResolveError(f"{uri!r}: the journal has {len(items)} decision(s)")
    return "\n".join(items[number - 1]).encode("utf-8")


def _apply_anchor(kind: str, anchor: str, content: bytes, uri: str) -> bytes:
    match = _DECISION_ANCHOR.fullmatch(anchor)
    if kind == "journal" and match:
        return _decision(content, int(match["n"]), uri)
    return content


# --- game types ---


def _resolve_game(canonical: Path, sha: str, game: Game, kind: str, rest: str, uri: str):
    if not rest:
        raise ResolveError(f"{uri!r}: a {kind} reference needs an id")
    env = os.environ | {GAME_ENV_WORKSPACE: str(canonical), GAME_ENV_REV: sha}
    command = [*binding_argv(game.reference_types[kind]), rest]
    try:
        result = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            check=False,
            timeout=GAME_RESOLVER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise ResolveError(
            f"{uri!r}: the {game.name} resolver did not finish in {GAME_RESOLVER_TIMEOUT_S} s"
        ) from None
    except OSError as error:
        raise ResolveError(f"{uri!r}: cannot run the {game.name} resolver: {error}") from None
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise ResolveError(f"{uri!r}: the {game.name} resolver found nothing: {stderr}")
    return Resolved(path=f"{kind}/{rest}", content=result.stdout, rev=sha)


# --- entry point ---


def resolve(
    canonical: Path, uri: str, rev: str | None = None, game: Game | None = None
) -> Resolved:
    """Resolve ``uri`` at ``rev`` (default HEAD) of ``canonical``; ``game`` adds its
    reference types. Raises ResolveError."""
    if not uri.startswith(SCHEME):
        raise ResolveError(f"not an ar:// reference: {uri!r}")
    target, _, anchor = uri.removeprefix(SCHEME).partition("#")
    kind, _, rest = target.partition("/")
    try:
        sha = gitops.git(canonical, "rev-parse", "--verify", f"{rev or 'HEAD'}^{{commit}}")
    except gitops.GitError as error:
        raise ResolveError(f"unknown revision {rev or 'HEAD'!r}") from error
    if kind not in CORE_TYPES:
        if game is not None and kind in game.reference_types:
            return _resolve_game(canonical, sha, game, kind, rest, uri)
        raise ResolveError(f"unknown reference type {kind!r} in {uri!r}")
    path = _path_for(canonical, sha, uri, kind, rest, anchor)
    content = gitops.show(canonical, sha, path)
    if content is None:
        raise ResolveError(f"{uri!r}: {path} does not exist at {sha[:12]}")
    return Resolved(path=path, content=_apply_anchor(kind, anchor, content, uri), rev=sha)
