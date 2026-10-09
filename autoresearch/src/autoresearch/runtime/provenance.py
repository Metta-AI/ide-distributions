"""Canonical commit provenance: find commits on canonical ``main`` that the runtime did not
make (Overview C1: queue-keeper and the trusted runtime commands are the only writers).

Every runtime writer of the canonical workspace makes each commit through ``commit_recorded``,
which records it in a journal outside the work tree, in ``<canonical>/.git/autoresearch/``:
``provenance.jsonl`` (append-only, one JSON object per line) and ``provenance.key`` (a random
key, mode 0600). Each entry carries ``seq`` (one more than the entry before it) and ``mac``,
an HMAC-SHA256 of its other fields under that key. An entry whose MAC does not verify, or
whose ``seq`` is not greater than the last valid entry's (a signed entry replayed at the end,
for example an old anchor appended again after a reset to it), is ignored: it vouches for
nothing. Truncating the journal itself (cutting off its newest entries) cannot be detected
from inside the workspace: that needs the latest ``seq`` kept where card workers cannot
write (D9 options (c) or (d)).

Entry kinds:

- ``intent``: written after the runtime built a commit object and before it moves ``main``
  to it: ``intent`` (a random id), ``sha`` (that exact commit), ``parent`` (HEAD then, or
  null), ``step`` (the commit subject), ``paths`` and ``merge`` (what settling the index
  after the move needs; ``recover``).
- ``commit``: one runtime commit: ``sha``, ``parent`` (its first parent, or null for a root
  commit), ``step`` (the commit subject, which names the runtime step), ``intent``, ``pid``,
  ``at``.
- ``void``: the intent's commit never landed (``main`` had moved, so the branch update was
  refused).
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
``_append`` ends a torn last line (no newline) before it writes, so the torn line stays one
invalid line, and ``accept`` checks that its anchor reads back.

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
between moving ``main`` and the ``commit`` entry leaves the ``intent`` open: a commit whose
sha an open intent names is the runtime's own (``check``). Only that exact sha: a commit
someone else makes on the same parent with the same subject is still reported. The next
queue-keeper run finishes such a commit before it checks (``recover``: the ``commit`` entry,
and the index entries of its paths set to HEAD)."""

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
ACCEPTED_REASON = "accepted by an operator"  # the ``reason`` of an accept-commits anchor
PAUSE_REASON = "commit provenance"  # the pause reason prefix (pause.json ``reason``)


class ProvenanceError(RuntimeError):
    """``accept`` was given a commit it cannot accept."""


class JournalError(RuntimeError):
    """The journal or its key cannot be read or written. Every runtime commit on canonical
    needs the journal, so callers treat this as the provenance check failing (fail closed).
    Code that contains per-item failures must not turn it into a string: keep it
    (``journal_error``) and raise it after the loop, so queue-keeper still pauses."""


def journal_error(error: BaseException | None) -> JournalError | None:
    """The ``JournalError`` behind ``error`` (itself, or along its cause/context chain)."""
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, JournalError):
            return error
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return None


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


def _append(canonical: Path, entry: dict) -> dict:
    """Append one signed entry and return it as written. Call under the canonical lock (every
    writer does), so no other writer appends in between. A last line without its newline (a
    writer killed mid-write) is ended first: it stays one invalid line instead of swallowing
    this entry. A short write is continued until the whole line is written."""
    try:
        key = _key(canonical, create=True)
        before, invalid = _entries(canonical)
        entry = entry | {"seq": _last_seq(before) + 1}
        signed = entry | {"mac": _mac(key, entry)}
        data = (json.dumps(signed, sort_keys=True) + "\n").encode()
        path = journal_path(canonical)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            size = os.fstat(descriptor).st_size
            if size and os.pread(descriptor, 1, size - 1) != b"\n":
                data = b"\n" + data
            remaining = memoryview(data)
            while remaining:
                remaining = remaining[os.write(descriptor, remaining) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Under the lock only this append changed the journal: extend the snapshot.
        _SNAPSHOTS[path] = (_signature(canonical), [*before, signed], invalid)
    except OSError as error:
        raise JournalError(f"cannot write the provenance journal: {error}") from error
    return signed


def probe(canonical: Path) -> None:
    """Raise ``JournalError`` unless the journal can be appended to now, without writing an
    entry: the key reads, and the journal opens for append (or its directory takes a new
    file). queue-keeper probes every run, so a read-only journal fails closed even on a
    run whose check would only read."""
    try:
        key = journal_dir(canonical) / KEY_FILE
        if key.exists():
            key.read_bytes()
        path = journal_path(canonical)
        if path.exists():
            descriptor = os.open(path, os.O_RDWR | os.O_APPEND)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        else:
            directory = path.parent if path.parent.exists() else path.parent.parent
            if not os.access(directory, os.W_OK | os.X_OK):
                raise PermissionError(f"{directory} is not writable")
    except OSError as error:
        raise JournalError(f"the provenance journal cannot be written: {error}") from error


def _last_seq(entries: list[dict]) -> int:
    return max((entry["seq"] for entry in entries if "seq" in entry), default=0)


# The last verified journal per path: (signature, valid entries, invalid count). Reused while
# the journal and key files are unchanged (inode, size, mtime, mode), and extended by this
# process's own appends, so a run does not re-verify the whole journal for every entry.
_SNAPSHOTS: dict[Path, tuple[tuple, list[dict], int]] = {}


def _signature(canonical: Path) -> tuple:
    stats = []
    for path in (journal_path(canonical), journal_dir(canonical) / KEY_FILE):
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            stats.append(None)
        else:
            stats.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_mode))
    return tuple(stats)


def _entries(canonical: Path, fresh: bool = False) -> tuple[list[dict], int]:
    """The valid entries in file order, and the number of lines that did not verify. An entry
    is valid when its MAC verifies and its ``seq`` is greater than every valid entry's before
    it, so a signed entry copied to the end again (a replay) is invalid. An entry without
    ``seq`` (written before sequence numbers) counts only before the first one that has it.
    Served from the snapshot while the files are unchanged, unless ``fresh``."""
    path = journal_path(canonical)
    try:
        signature = _signature(canonical)
        cached = _SNAPSHOTS.get(path)
        if not fresh and cached is not None and cached[0] == signature:
            return list(cached[1]), cached[2]
        if not path.exists():
            return [], 0
        key = _key(canonical, create=False)
        content = path.read_bytes()
    except OSError as error:
        raise JournalError(f"cannot read the provenance journal: {error}") from error
    valid, invalid, last = [], 0, None
    for line in content.splitlines():
        if not line.strip():
            continue
        try:  # decoding is inside: one bad byte must not stop the check (it fails closed)
            entry = json.loads(line.decode("utf-8"))
            ok = key is not None and hmac.compare_digest(str(entry.get("mac")), _mac(key, entry))
            seq = entry.get("seq")
            if ok and seq is None:
                ok = last is None
            elif ok:
                ok = isinstance(seq, int) and (last is None or seq > last)
        except (ValueError, AttributeError, TypeError):
            ok = False
        if ok:
            valid.append(entry)
            last = entry.get("seq", last)
        else:
            invalid += 1
    _SNAPSHOTS[path] = (signature, valid, invalid)
    return list(valid), invalid


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
    canonical: Path, message: str, prepare: Callable[[], gitops.PreparedCommit | None]
) -> str | None:
    """Make one runtime commit on canonical and record it; return its sha, or None when
    ``prepare`` found nothing to commit. Call under the canonical lock.

    ``prepare`` builds the commit object without moving any branch (``gitops.prepare_*``).
    The ``intent`` entry names that exact commit before HEAD moves to it, then the
    ``commit`` entry follows: a crash in between is recognized, and an intent vouches for no
    other commit. A failure before the intent writes nothing; a move of HEAD that fails
    (``gitops.land`` is all or nothing) closes the intent with a ``void`` entry. After the
    move the index is settled before the ``commit`` entry is written, so a closed intent is
    always a settled one."""
    prepared = prepare()
    if prepared is None:
        return None
    intent = secrets.token_hex(8)
    try:
        _append(
            canonical,
            {"kind": INTENT, "intent": intent, "sha": prepared.sha, "parent": prepared.parent,
             "step": _subject(message), "paths": list(prepared.paths), "merge": prepared.merge,
             "at": _now()},
        )  # fmt: skip
    except BaseException:
        gitops.discard(canonical, prepared)  # leave no merge in progress behind
        raise
    try:
        gitops.land(canonical, prepared)
    except gitops.GitError:
        _append(canonical, {"kind": VOID, "intent": intent, "at": _now()})
        raise
    # Settle, then record: the commit entry closes the intent, so it comes last. A crash or
    # failure before it leaves the intent open, and ``recover`` settles (again; idempotent)
    # and records it on the next run.
    gitops.settle(canonical, prepared)
    record(canonical, prepared.sha, message, intent)
    return prepared.sha


def _merging(canonical: Path) -> bool:
    try:
        gitops.git(canonical, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    except gitops.GitError:
        return False
    return True


def recover(canonical: Path) -> list[str]:
    """Finish runtime commits a crash left between moving ``main`` and their ``commit``
    entry: an open intent whose commit is in HEAD's history gets its ``commit`` entry, and
    the index entries of its paths are set to HEAD (an unsettled commit leaves them staged
    as a revert; other entries are left alone) or its merge is ended. Call under the
    canonical lock, before ``check`` (queue-keeper does, every run). Returns the commits
    recovered. An intent whose commit never landed stays open: it vouches for that exact
    commit only."""
    entries, _ = _entries(canonical)
    closed = {entry.get("intent") for entry in entries if entry["kind"] in (COMMIT, VOID)}
    head = gitops.head(canonical)
    recovered = []
    for entry in entries:
        if entry["kind"] != INTENT or entry["intent"] in closed or "sha" not in entry:
            continue
        if not _is_ancestor(canonical, entry["sha"], head):
            continue
        # Settle first (idempotent), record last: a crash in between is retried next run.
        if entry.get("merge"):
            if _merging(canonical):
                gitops.git(canonical, "merge", "--quit")
        elif entry.get("paths"):
            gitops.git(canonical, "reset", "-q", "HEAD", "--", *entry["paths"])
        record(canonical, entry["sha"], entry["step"], entry["intent"])
        recovered.append(entry["sha"])
    return recovered


def _anchor(canonical: Path, sha: str, reason: str) -> dict:
    return _append(canonical, {"kind": ANCHOR, "sha": sha, "reason": reason, "at": _now()})


def operator_anchors(canonical: Path) -> list[dict]:
    """Valid ``accept-commits`` anchors (``sha``, ``at``), oldest first. queue-keeper announces
    each once, so an accept is never silent, also one forged with the key."""
    return [
        entry
        for entry in _entries(canonical)[0]
        if entry["kind"] == ANCHOR and entry.get("reason") == ACCEPTED_REASON
    ]


def _base(entries: list[dict]) -> int | None:
    """The index of the last anchor or verified entry."""
    for index in range(len(entries) - 1, -1, -1):
        if entries[index]["kind"] in (ANCHOR, VERIFIED):
            return index
    return None


# --- bootstrap, check, accept ---


def _ever_marked(canonical: Path) -> bool:
    """The committed marker is, or ever was, in HEAD's history: the journal was initialized."""
    return bool(gitops.git(canonical, "log", "-1", "--format=%H", "--", MARKER_RELATIVE))


def initialize(canonical: Path, fresh: bool) -> str | None:
    """Bootstrap, under the canonical lock: trust the current HEAD once when the journal has
    no valid anchor and either the workspace is ``fresh`` (seeded or cloned by this run) or
    it was never initialized (no committed marker). The marker is committed then, so a later
    loss of the journal is reported (``journal_lost``) instead of trusted again. A journal
    that is anchored but whose marker was never committed (a crash between the two) gets the
    marker now, with no new anchor. Returns a line for the bootstrap log, or None when
    nothing was done."""
    entries, _ = _entries(canonical)
    base = _base(entries)
    if base is not None:
        if _ever_marked(canonical):
            return None
        _commit_marker(canonical, entries[base]["sha"])
        return "provenance marker committed (the journal was already anchored)"
    has_marker = gitops.show(canonical, "HEAD", MARKER_RELATIVE) is not None
    if _ever_marked(canonical) and not fresh:
        return None  # check reports journal_lost, also when the marker was deleted since
    head = gitops.head(canonical)
    _anchor(canonical, head, "bootstrap" if fresh else "bootstrap: existing workspace")
    if not has_marker:
        _commit_marker(canonical, head)
    return f"provenance journal initialized: trusted HEAD {head}"


def _commit_marker(canonical: Path, trusted_head: str) -> None:
    marker = Path(canonical) / MARKER_RELATIVE
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"initialized_at": _now(), "trusted_head": trusted_head}, indent=2,
                   sort_keys=True) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    subject = "provenance: journal initialized"
    commit_recorded(
        canonical, subject, lambda: gitops.prepare_paths(canonical, [MARKER_RELATIVE], subject)
    )


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


def check(canonical: Path, advance: bool = True, head: str | None = None) -> Violation | None:
    """Check canonical HEAD's (or ``head``'s) first-parent history since the last anchored
    or verified commit (module docstring). Call under the canonical lock. With ``advance``,
    a clean history moves the verified point to HEAD."""
    entries, _ = _entries(canonical)
    head = head or gitops.head(canonical)
    base_index = _base(entries)
    if base_index is None:
        return Violation(JOURNAL_LOST, head, None)
    base = entries[base_index]["sha"]
    recorded = {entry["sha"]: entry for entry in entries if entry["kind"] == COMMIT}
    # An anchor older than the last verified commit (an operator accepted an old commit)
    # restarts the walk there; commits anchored or verified before still count.
    trusted = {entry["sha"] for entry in entries if entry["kind"] in (ANCHOR, VERIFIED)}
    closed = {entry.get("intent") for entry in entries if entry["kind"] in (COMMIT, VOID)}
    # An intent names the exact commit it was about to land, so an open one (a writer
    # crashed between moving HEAD and its commit entry) vouches for that commit only.
    open_intents = {
        entry.get("sha")
        for entry in entries
        if entry["kind"] == INTENT and entry["intent"] not in closed
    }

    def is_runtime(sha: str) -> bool:
        if sha in trusted or sha in open_intents:
            return True
        if sha in recorded:
            return recorded[sha]["parent"] == _first_parent(canonical, sha)
        return False

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
    # made since the last check were reset or rebased away. One the base already covers (an
    # ancestor of an accepted commit, recorded late by ``recover``) is not lost.
    since = set(walked)
    lost = [
        entry["sha"]
        for entry in entries[base_index + 1 :]
        if entry["kind"] == COMMIT
        and entry["sha"] not in since
        and not _is_ancestor(canonical, entry["sha"], base)
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
    written = _anchor(canonical, full, ACCEPTED_REASON)
    entries, _ = _entries(canonical, fresh=True)  # read the file back, not the snapshot
    if not entries or entries[-1] != written:  # under the lock nothing else appended
        raise ProvenanceError(
            f"the accept of {full} was written but does not read back from the journal "
            f"{journal_path(canonical)}; the pause stays. Check the journal (runtime "
            "check-commits shows invalid lines) and run accept-commits again"
        )
    return {"accepted": full, "head": head}


# --- pushing main ---


def push_hold(canonical: Path, sha: str | None = None) -> str | None:
    """Why canonical ``main`` (at ``sha``, default HEAD) must not be pushed now, or None.
    While a violation is unresolved (or the check cannot run) the commits nobody recorded
    stay local: pushing would publish them to the remote before a person accepted them.
    Messages still reach the coach: the bridge reads ``comms/outbox/`` from the local
    workspace. A workspace the journal was never initialized on (no anchor line in the
    journal, valid or not, and no marker ever committed: bootstrap's first push of a seeded
    workspace, which ``initialize`` then trusts) is not held."""
    try:
        violation = check(canonical, advance=False, head=sha)
        never_initialized = (
            violation is not None
            and violation.kind == JOURNAL_LOST
            and not _ever_marked(canonical)
            and not _any_anchor_line(canonical)
        )
        if never_initialized:
            return None  # bootstrap's seed push, before ``initialize``
    except Exception as error:  # noqa: BLE001 - a check that cannot run holds the push
        return f"the provenance check cannot run: {type(error).__name__}: {error}"
    return None if violation is None else violation.summary()


def _any_anchor_line(canonical: Path) -> bool:
    """The journal holds an anchor line, valid or not: it was initialized once (a journal
    whose anchor stopped verifying is not "never initialized")."""
    path = journal_path(canonical)
    if not path.exists():
        return False
    for line in path.read_bytes().splitlines():
        try:
            if json.loads(line).get("kind") == ANCHOR:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def push_main(canonical: Path, remote: str) -> bool:
    """Every push of canonical ``main`` goes through here. It resolves HEAD to one sha,
    checks that sha (``push_hold``) and pushes exactly it (``<sha>:refs/heads/main``), so a
    commit made between the check and the push is not published unchecked. Call under the
    canonical lock. Returns False when the push is held or fails; the next push retries."""
    sha = gitops.head(canonical)
    if push_hold(canonical, sha) is not None:
        return False
    return gitops.push(canonical, remote, [f"{sha}:refs/heads/main"])
