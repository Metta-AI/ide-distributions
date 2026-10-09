"""Canonical commit provenance: find commits on canonical ``main`` that the runtime did not
make (Overview C1: queue-keeper and the trusted runtime commands are the only writers).

Every runtime writer of the canonical workspace makes each commit through ``commit_recorded``,
which records it in a journal outside the work tree, in ``<canonical>/.git/autoresearch/``:
``provenance.jsonl`` (append-only, one JSON object per line) and ``provenance.key`` (a random
key, mode 0600). Each entry carries ``mac``, an HMAC-SHA256 of its other fields under that
key. An entry whose MAC does not verify is ignored: it vouches for nothing.

Entry kinds:

- ``intent``: written just before a runtime commit: ``intent`` (a random id), ``parent``
  (HEAD then, or null), ``step`` (the commit subject).
- ``commit``: one runtime commit: ``sha``, ``parent`` (its first parent, or null for a root
  commit), ``step`` (the commit subject, which names the runtime step), ``intent``, ``pid``,
  ``at``.
- ``void``: the intent made no commit (nothing to commit, or a merge that conflicted).
- ``anchor``: trust ``sha`` and all of its history. Bootstrap writes one when it initializes
  the journal (``initialize``: a new or restored workspace, or the first run of this check on
  an existing one), and the operator command ``runtime accept-commits`` writes one.
- ``verified``: queue-keeper checked the history up to ``sha``.

``check`` (queue-keeper, every run, before it acts; under the canonical lock) walks the
first-parent history of HEAD since the last ``anchor`` or ``verified`` entry. Each commit
needs a ``commit`` entry with the same first parent. Otherwise it returns a ``Violation``:

- ``unknown_commits``: commits that no runtime writer recorded (for example a card worker
  that committed on canonical itself, working around the plugin);
- ``rewritten``: the last checked commit, or a runtime commit recorded after it, is no longer
  in HEAD's history (a reset, rebase, amend or forced move of ``main``);
- ``journal_lost``: no valid anchor, but the workspace says the journal was initialized
  (``.supervisor/ledger/provenance.json`` is, or ever was, in HEAD's history): the journal or
  its key was deleted or replaced.

A line that is not valid JSON, not UTF-8, or whose MAC does not verify vouches for nothing;
``invalid_lines`` counts them (queue-keeper logs the count, ``runtime check-commits`` shows it).

What this is not. Every process of the profile runs as one OS user, and card workers write
the journal legitimately (``card finish`` commits the handoff on canonical), so the OS cannot
hide the key from them. The plugin refuses model tool calls that name the journal directory,
its files or this module (best effort, ``plugin/policy.py``), but a process that gets the key
anyway can write valid entries. So the journal makes a commit made outside the runtime
visible unless its author also forges the journal on purpose; it is not a security boundary.
The boundary is a separate OS user for card workers, without write access to canonical
(README, "Known gaps").

A runtime writer records its commit before it releases the canonical lock, and ``check``
runs under that lock, so a check never sees a runtime commit without its entry. A crash
between a commit and its ``commit`` entry leaves the ``intent`` open: an unrecorded commit
whose first parent and subject match an open intent is the runtime's own (``check``).
"""

import datetime
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.io import gitops

JOURNAL_DIR = Path(".git") / "autoresearch"
JOURNAL_FILE = "provenance.jsonl"
KEY_FILE = "provenance.key"
MARKER_RELATIVE = ".supervisor/ledger/provenance.json"
COMMIT, ANCHOR, VERIFIED = "commit", "anchor", "verified"
INTENT, VOID = "intent", "void"
UNKNOWN_COMMITS, REWRITTEN, JOURNAL_LOST = "unknown_commits", "rewritten", "journal_lost"
PAUSE_REASON = "commit provenance"  # the pause reason prefix (pause.json ``reason``)


class ProvenanceError(RuntimeError):
    """``accept`` was given a commit it cannot accept."""


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author: str
    at: str  # author time, ISO 8601
    subject: str
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "author": self.author,
            "at": self.at,
            "subject": self.subject,
            "files": list(self.files),
        }


@dataclass(frozen=True)
class Violation:
    kind: str  # UNKNOWN_COMMITS | REWRITTEN | JOURNAL_LOST
    head: str
    base: str | None  # the last anchored or verified commit, if any
    commits: list[CommitInfo] = field(default_factory=list)  # unrecorded, oldest first
    dropped: int = 0  # REWRITTEN: commits of the checked history that HEAD no longer has

    @property
    def accept_sha(self) -> str:
        """The commit ``runtime accept-commits`` should be given: the newest unrecorded
        commit, so a commit made after this check is not accepted unseen; else HEAD."""
        if self.kind == UNKNOWN_COMMITS and self.commits:
            return self.commits[-1].sha
        return self.head

    def summary(self) -> str:
        if self.kind == UNKNOWN_COMMITS:
            return f"{len(self.commits)} commit(s) on canonical main not made by the runtime"
        if self.kind == REWRITTEN:
            return f"canonical main was rewritten ({self.dropped} checked commit(s) gone)"
        return "the runtime's commit journal is missing or does not verify"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "head": self.head,
            "base": self.base,
            "accept_sha": self.accept_sha,
            "dropped": self.dropped,
            "commits": [commit.to_dict() for commit in self.commits],
        }


# --- the journal ---


def journal_dir(canonical: Path) -> Path:
    return Path(canonical) / JOURNAL_DIR


def journal_path(canonical: Path) -> Path:
    return journal_dir(canonical) / JOURNAL_FILE


def _key(canonical: Path, create: bool) -> bytes | None:
    """The journal key; with ``create``, make one if there is none (atomically, so two
    writers starting at once agree on one key)."""
    path = journal_dir(canonical) / KEY_FILE
    if path.exists() or not create:
        return path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{KEY_FILE}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(secrets.token_hex(32).encode())
    try:
        os.link(temporary, path)  # fails if another writer made the key first
    except FileExistsError:
        pass
    finally:
        temporary.unlink()
    return path.read_bytes()


def _mac(key: bytes, entry: dict) -> str:
    fields = {name: value for name, value in entry.items() if name != "mac"}
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _append(canonical: Path, entry: dict) -> None:
    key = _key(canonical, create=True)
    line = json.dumps(entry | {"mac": _mac(key, entry)}, sort_keys=True) + "\n"
    path = journal_path(canonical)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write of one line in append mode: concurrent writers never interleave inside a line.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, line.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _entries(canonical: Path) -> tuple[list[dict], int]:
    """The valid entries in file order, and the number of lines that did not verify."""
    path = journal_path(canonical)
    if not path.exists():
        return [], 0
    key = _key(canonical, create=False)
    valid, invalid = [], 0
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        try:  # decoding is inside: one bad byte must not stop the check (it fails closed)
            entry = json.loads(line.decode("utf-8"))
            ok = key is not None and hmac.compare_digest(str(entry.get("mac")), _mac(key, entry))
        except (ValueError, AttributeError, TypeError):
            ok = False
        if ok:
            valid.append(entry)
        else:
            invalid += 1
    return valid, invalid


def _first_parent(canonical: Path, sha: str) -> str | None:
    try:
        return gitops.git(canonical, "rev-parse", "--verify", "-q", f"{sha}^1")
    except gitops.GitError:
        return None  # a root commit


def invalid_lines(canonical: Path) -> int:
    """Journal lines that vouch for nothing (module docstring)."""
    return _entries(canonical)[1]


def _subject(message: str) -> str:
    return message.splitlines()[0] if message else ""


def record(canonical: Path, sha: str, step: str, intent: str | None = None) -> None:
    """Record a commit the runtime just made on canonical. Call it before releasing the
    canonical lock (module docstring). Runtime code commits through ``commit_recorded``."""
    _append(
        canonical,
        {
            "kind": COMMIT,
            "sha": sha,
            "parent": _first_parent(canonical, sha),
            "step": _subject(step),
            "intent": intent,
            "pid": os.getpid(),
            "at": _now(),
        },
    )


def commit_recorded(
    canonical: Path, message: str, make_commit: Callable[[], str | None]
) -> str | None:
    """Make one runtime commit on canonical with ``make_commit`` (which returns the new
    commit, or None when it made none) and record it: an ``intent`` entry first, then a
    ``commit`` entry (or ``void``), so a crash in between is still recognized. Call under the
    canonical lock."""
    intent = secrets.token_hex(8)
    try:
        parent = gitops.head(canonical)
    except gitops.GitError:
        parent = None  # an empty repository: the first commit has no parent
    _append(
        canonical,
        {"kind": INTENT, "intent": intent, "parent": parent, "step": _subject(message),
         "at": _now()},
    )  # fmt: skip
    sha = make_commit()
    if sha is None:
        _append(canonical, {"kind": VOID, "intent": intent, "at": _now()})
    else:
        record(canonical, sha, message, intent)
    return sha


def _anchor(canonical: Path, sha: str, reason: str) -> None:
    _append(canonical, {"kind": ANCHOR, "sha": sha, "reason": reason, "at": _now()})


def _base(entries: list[dict]) -> int | None:
    """The index of the last anchor or verified entry."""
    for index in range(len(entries) - 1, -1, -1):
        if entries[index]["kind"] in (ANCHOR, VERIFIED):
            return index
    return None


# --- bootstrap, check, accept ---


def initialize(canonical: Path, fresh: bool) -> str | None:
    """Bootstrap, under the canonical lock: trust the current HEAD once when the journal has
    no valid anchor and either the workspace is ``fresh`` (seeded or cloned by this run) or
    it was never initialized (no committed marker). The marker is committed then, so a later
    loss of the journal is reported (``journal_lost``) instead of trusted again. Returns a
    line for the bootstrap log, or None when nothing was done."""
    entries, _ = _entries(canonical)
    if _base(entries) is not None:
        return None
    has_marker = gitops.show(canonical, "HEAD", MARKER_RELATIVE) is not None
    ever_marked = bool(gitops.git(canonical, "log", "-1", "--format=%H", "--", MARKER_RELATIVE))
    if ever_marked and not fresh:
        return None  # check reports journal_lost, also when the marker was deleted since
    head = gitops.head(canonical)
    _anchor(canonical, head, "bootstrap" if fresh else "bootstrap: existing workspace")
    if not has_marker:
        marker = Path(canonical) / MARKER_RELATIVE
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"initialized_at": _now(), "trusted_head": head}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        subject = "provenance: journal initialized"
        commit_recorded(
            canonical, subject, lambda: gitops.commit_paths(canonical, [MARKER_RELATIVE], subject)
        )
    return f"provenance journal initialized: trusted HEAD {head}"


def describe(canonical: Path, sha: str) -> CommitInfo:
    shown = gitops.git(canonical, "show", "-s", "--format=%an <%ae>%x00%aI%x00%s", sha)
    author, at, subject = shown.split("\0", 2)
    files = gitops.git(
        canonical, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m",
        "--first-parent", sha,
    ).splitlines()
    return CommitInfo(sha, author, at, subject, sorted(set(files)))


def _first_parent_since(canonical: Path, since: str, head: str) -> list[str]:
    """First-parent commits after ``since`` up to ``head``, oldest first."""
    listing = gitops.git(canonical, "rev-list", "--first-parent", "--reverse", f"{since}..{head}")
    return listing.split()


def _is_ancestor(canonical: Path, sha: str, of: str) -> bool:
    try:
        gitops.git(canonical, "merge-base", "--is-ancestor", sha, of)
    except gitops.GitError:
        return False  # not an ancestor, or ``sha`` is no longer in the repository
    return True


def check(canonical: Path, advance: bool = True) -> Violation | None:
    """Check canonical HEAD's first-parent history since the last anchored or verified
    commit (module docstring). Call under the canonical lock. With ``advance``, a clean
    history moves the verified point to HEAD."""
    entries, _ = _entries(canonical)
    head = gitops.head(canonical)
    base_index = _base(entries)
    if base_index is None:
        return Violation(JOURNAL_LOST, head, None)
    base = entries[base_index]["sha"]
    recorded = {entry["sha"]: entry for entry in entries if entry["kind"] == COMMIT}
    # An anchor older than the last verified commit (an operator accepted an old commit)
    # restarts the walk there; commits anchored or verified before still count.
    trusted = {entry["sha"] for entry in entries if entry["kind"] in (ANCHOR, VERIFIED)}
    closed = {entry.get("intent") for entry in entries if entry["kind"] in (COMMIT, VOID)}
    open_intents = {
        (entry["parent"], entry["step"])
        for entry in entries
        if entry["kind"] == INTENT and entry["intent"] not in closed
    }

    def is_runtime(sha: str) -> bool:
        if sha in trusted:
            return True
        parent = _first_parent(canonical, sha)
        if sha in recorded:
            return recorded[sha]["parent"] == parent
        # A runtime writer crashed between its commit and its commit entry.
        subject = gitops.git(canonical, "show", "-s", "--format=%s", sha)
        return (parent, subject) in open_intents

    def unrecorded(shas: list[str]) -> list[CommitInfo]:
        return [describe(canonical, sha) for sha in shas if not is_runtime(sha)]

    if not _is_ancestor(canonical, base, head):
        try:
            fork = gitops.merge_base(canonical, base, head)
            dropped = len(_first_parent_since(canonical, fork, base))
            added = _first_parent_since(canonical, fork, head)
        except gitops.GitError:  # the old commit is gone, or the histories share nothing
            dropped, added = 0, []
        return Violation(REWRITTEN, head, base, unrecorded(added), dropped)
    walked = _first_parent_since(canonical, base, head)
    # A runtime commit recorded after the base but no longer in HEAD's history: the commits
    # made since the last check were reset or rebased away.
    lost = [
        entry["sha"]
        for entry in entries[base_index + 1 :]
        if entry["kind"] == COMMIT and entry["sha"] not in set(walked) and entry["sha"] != base
    ]
    if lost:
        return Violation(REWRITTEN, head, base, unrecorded(walked), len(lost))
    commits = unrecorded(walked)
    if commits:
        return Violation(UNKNOWN_COMMITS, head, base, commits)
    if advance and head != base:
        _append(canonical, {"kind": VERIFIED, "sha": head, "at": _now()})
    return None


def accept(canonical: Path, sha: str) -> dict:
    """Operator only (``runtime accept-commits``), under the canonical lock: trust ``sha``
    and its whole history. ``sha`` must be HEAD or in HEAD's history."""
    try:
        full = gitops.git(canonical, "rev-parse", "--verify", f"{sha}^{{commit}}")
    except gitops.GitError:
        raise ProvenanceError(f"{sha} is not a commit in the canonical workspace") from None
    head = gitops.head(canonical)
    if not _is_ancestor(canonical, full, head):
        raise ProvenanceError(f"{full} is not in the history of canonical HEAD {head}")
    _anchor(canonical, full, "accepted by an operator")
    return {"accepted": full, "head": head}
