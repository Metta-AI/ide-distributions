"""Crash-safe outbox publication (Overview C5). Runtime only.

Files (all in canonical):

- ``comms/staged/<M-id>.json``: staged by a card worker; arrives in canonical with the merge.
- ``comms/outbox/<seq:08d>-<M-id>.json``: the published, immutable message.
- ``.supervisor/ledger/published/<M-id>.json``: the publication receipt
  ``{id, seq, file, published_rev, from, published_at}``.

Sequence numbers. ``next_seq = 1 + max(seq of the files in comms/outbox/)``: no counter, only
the outbox files themselves. Publishing one message writes, in this order, the outbox file,
the receipt, and (for a staged message) the removal of the staged file; one commit then
covers every message of the call, and a push of ``main`` to ``origin`` follows. A failed
push keeps the local commit, and every later publication pushes again; the result reports
``pushed`` so queue-keeper can raise a help_request on repeated failures (Task 3.9). A
missing ``origin`` is a configuration error (``PublishError``) unless the caller passes
``allow_no_remote=True`` (tests, local runs).

The ``@result`` rev of a staged message is the first-parent commit of canonical that added
its staged file (normally the merge commit of its card), not simply the merge being
published now: a message left over from an earlier merge keeps that merge's revision. A
staged message that fails the envelope is skipped (left staged) and reported in the result;
the rest still publish.

Recovery (``recover``, run first by every publication): an outbox file with no receipt gets
its receipt; a receipt with no outbox file gets the file rebuilt from the staged message; a
staged file with a receipt is removed; then the leftovers are committed. Because the outbox
file is written first and recovery completes it before any new number is taken, each message
gets exactly one sequence number across crashes, with no gaps.

Readers see only committed outbox files (``reader``), never a half-done publication.

Locking. Every function here must run under ``canonical.lock`` held by the caller, and never
takes it: ``process_handoffs`` calls ``publish_hook`` while it holds the (non-reentrant) lock.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.comms.envelope import (
    FROM_AGENT,
    FROM_RUNTIME,
    RESULT_REV,
    MessageProblems,
    build_message,
    check_message,
    now_iso,
    ref_dict,
)
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json

OUTBOX_DIR = "comms/outbox"
STAGED_DIR = "comms/staged"
RECEIPTS_DIR = ".supervisor/ledger/published"
REMOTE = "origin"
BRANCH = "main"
OUTBOX_NAME = re.compile(r"(?P<seq>\d{8})-(?P<id>M-[0-9A-HJKMNP-TV-Z]{26})\.json")


class PublishError(RuntimeError):
    """Publication cannot proceed: no ``origin`` remote (and none allowed), or state that
    recovery cannot repair (a receipt whose message is lost)."""


@dataclass
class PublishResult:
    seqs: list[int] = field(default_factory=list)  # new sequence numbers, in order
    pushed: bool = False  # main reached origin (False also when no remote was allowed)
    skipped: dict[str, list[str]] = field(default_factory=dict)  # invalid staged M-id -> problems

    @property
    def seq(self) -> int:
        """The single sequence number of a ``publish_runtime`` call."""
        (seq,) = self.seqs
        return seq


def _checkpoint(step: str) -> None:
    """Test seam: crash-injection tests replace this to stop after ``step``
    (``outbox_written``, ``receipt_written``, ``staged_removed``, ``before_commit``,
    ``before_push``)."""


# --- files ---


def outbox_name(seq: int, message_id: str) -> str:
    return f"{seq:08d}-{message_id}.json"


def _outbox_files(canonical: Path) -> dict[str, tuple[int, Path]]:
    """Message id -> (seq, path) for the outbox files on disk."""
    found = {}
    for path in sorted((canonical / OUTBOX_DIR).glob("*.json")):
        match = OUTBOX_NAME.fullmatch(path.name)
        if match:
            found[match["id"]] = (int(match["seq"]), path)
    return found


def receipt_path(canonical: Path, message_id: str) -> Path:
    return canonical / RECEIPTS_DIR / f"{message_id}.json"


def _staged_path(canonical: Path, message_id: str) -> Path:
    return canonical / STAGED_DIR / f"{message_id}.json"


def _next_seq(canonical: Path) -> int:
    return 1 + max((seq for seq, _ in _outbox_files(canonical).values()), default=0)


def _write_receipt(canonical: Path, message: dict, outbox_file: Path) -> None:
    receipt = {
        "id": message["id"],
        "seq": message["seq"],
        "file": outbox_file.relative_to(canonical).as_posix(),
        "published_rev": message.get("published_rev"),
        "from": message.get("from"),
        "published_at": now_iso(),
    }
    write_json(receipt_path(canonical, message["id"]), receipt)


def _published_form(message: dict, seq: int, rev: str) -> dict:
    """``message`` as published: numbered, at ``rev``, every ``@result`` ref rev replaced."""
    refs = [
        ref | {"rev": rev} if ref.get("rev") in (RESULT_REV, None) else ref
        for ref in (ref_dict(ref) for ref in message["refs"])
    ]
    return message | {"refs": refs, "seq": seq, "published_rev": rev}


def _write_outbox(canonical: Path, message: dict) -> None:
    """Write the outbox file, then the receipt, then remove the staged copy (if any)."""
    check_message(message, outbox=True)
    outbox_file = canonical / OUTBOX_DIR / outbox_name(message["seq"], message["id"])
    write_json(outbox_file, message)
    _checkpoint("outbox_written")
    _write_receipt(canonical, message, outbox_file)
    _checkpoint("receipt_written")
    _staged_path(canonical, message["id"]).unlink(missing_ok=True)
    _checkpoint("staged_removed")


# --- git ---


def _changed_paths(canonical: Path, directory: str) -> list[str]:
    """Paths under ``directory`` that differ from HEAD (new, changed or deleted)."""
    output = gitops.git(
        canonical, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", directory
    )
    entries = [entry for entry in output.split("\0") if entry]
    paths, skip = [], False
    for entry in entries:
        if skip:  # the source path of a rename/copy entry
            skip = False
            continue
        paths.append(entry[3:])
        skip = entry[0] in "RC"
    return paths


def _staged_at_head(canonical: Path) -> list[str]:
    """Staged message ids committed at HEAD, oldest id first. Only these are published: a
    staged message reaches canonical by a merge, never by an uncommitted write."""
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{STAGED_DIR}/")
    names = [path.rsplit("/", 1)[-1] for path in listing.splitlines()]
    return sorted(name.removesuffix(".json") for name in names if name.endswith(".json"))


def _commit_pending(canonical: Path, message: str) -> str | None:
    """Commit the outbox files, receipts and staged-file removals that differ from HEAD."""
    staged_removed = [
        f"{STAGED_DIR}/{message_id}.json"
        for message_id in _staged_at_head(canonical)
        if not _staged_path(canonical, message_id).exists()
    ]
    paths = [*_changed_paths(canonical, OUTBOX_DIR), *_changed_paths(canonical, RECEIPTS_DIR)]
    paths += staged_removed
    if not paths:
        return None
    return gitops.commit_paths(canonical, sorted(set(paths)), message)


def push(canonical: Path, allow_no_remote: bool = False) -> bool:
    """Push ``main`` to ``origin``; True if it got there. Without an ``origin`` remote,
    raise PublishError, or return False when ``allow_no_remote``."""
    if REMOTE not in gitops.git(canonical, "remote").split():
        if allow_no_remote:
            return False
        raise PublishError(f"canonical {canonical} has no {REMOTE!r} remote to push to")
    return gitops.push(canonical, REMOTE, [BRANCH])


def commit_and_push(canonical: Path, paths: list[Path], message: str) -> bool:
    """Commit exactly ``paths`` (absolute, under canonical) and push; for the other comms
    writes (inbox, seen ledger). A missing remote is tolerated here: the commit is durable
    and queue-keeper pushes ``main`` every run. Returns whether the push happened. Call under
    the canonical lock."""
    relative = [Path(path).relative_to(canonical).as_posix() for path in paths]
    gitops.commit_paths(canonical, relative, message)
    return push(canonical, allow_no_remote=True)


# --- recovery and publication ---


def _rebuild_outbox_file(canonical: Path, message_id: str, receipt: dict) -> None:
    content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
    staged = _staged_path(canonical, message_id)
    if content is None and staged.exists():
        content = staged.read_bytes()
    if content is None:
        raise PublishError(
            f"receipt {receipt_path(canonical, message_id)} has no outbox file and no staged "
            "message to rebuild it from"
        )
    message = _published_form(
        _as_agent(json.loads(content)), receipt["seq"], receipt["published_rev"]
    )
    write_json(canonical / OUTBOX_DIR / outbox_name(receipt["seq"], message_id), message)


def _receipts(canonical: Path) -> dict[str, dict]:
    return {path.stem: read_json(path) for path in (canonical / RECEIPTS_DIR).glob("M-*.json")}


def recover(canonical: Path) -> None:
    """Complete any publication a crash interrupted, then commit the leftovers (C5)."""
    outbox = _outbox_files(canonical)
    receipts = _receipts(canonical)
    for message_id, (seq, path) in outbox.items():
        if message_id not in receipts:
            message = read_json(path)
            message = message if isinstance(message, dict) else {}
            _write_receipt(canonical, message | {"id": message_id, "seq": seq}, path)
            receipts[message_id] = read_json(receipt_path(canonical, message_id))
    for message_id, receipt in receipts.items():
        if message_id not in outbox:
            _rebuild_outbox_file(canonical, message_id, receipt)
    for message_id in receipts:
        _staged_path(canonical, message_id).unlink(missing_ok=True)
    _commit_pending(canonical, "publish: recover interrupted publication")


def _as_agent(message: dict) -> dict:
    """A staged message is always from the research agent: a worker cannot publish as the
    runtime, whatever its file says."""
    return {**message, "from": FROM_AGENT}


def _added_by(canonical: Path, message_id: str) -> str | None:
    """The first-parent commit of canonical that added this staged file (its card's merge)."""
    sha = gitops.git(
        canonical,
        "log",
        "--first-parent",
        "--diff-filter=A",
        "-1",
        "--format=%H",
        "--",
        f"{STAGED_DIR}/{message_id}.json",
    )
    return sha or None


def _staged_message(canonical: Path, message_id: str, seq: int, fallback_rev: str) -> dict:
    """The staged message in published form; raises MessageProblems if it is invalid."""
    content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
    try:
        staged = json.loads(content)
    except ValueError as error:
        raise MessageProblems([f"not valid JSON: {error}"]) from None
    if not isinstance(staged, dict):
        raise MessageProblems(["a message must be a JSON object"])
    rev = _added_by(canonical, message_id) or fallback_rev
    message = _published_form(_as_agent(staged), seq, rev)
    check_message(message, outbox=True)
    return message


def publish_staged(canonical: Path, merge_sha: str, allow_no_remote: bool = False) -> PublishResult:
    """Publish every staged message committed in canonical (they arrive by merges). Each
    message's ``@result`` becomes the commit that added it (``merge_sha`` only if git finds
    none). Invalid messages are skipped and reported. Idempotent: a rerun publishes nothing
    twice and pushes again. Raises PublishError (no remote, unrepairable state)."""
    recover(canonical)
    receipts = _receipts(canonical)
    result = PublishResult()
    next_seq = _next_seq(canonical)
    for message_id in _staged_at_head(canonical):
        if message_id in receipts:  # recover already removed it
            continue
        try:
            message = _staged_message(canonical, message_id, next_seq, merge_sha)
        except MessageProblems as error:
            result.skipped[message_id] = error.problems
            continue
        _write_outbox(canonical, message)
        result.seqs.append(next_seq)
        next_seq += 1
    _checkpoint("before_commit")
    _commit_pending(canonical, f"publish {len(result.seqs)} message(s) after {merge_sha[:12]}")
    _checkpoint("before_push")
    result.pushed = push(canonical, allow_no_remote)
    return result


def publish_runtime(
    canonical: Path,
    kind: str,
    title: str,
    body: str,
    refs: list,
    allow_no_remote: bool = False,
    **fields,
) -> PublishResult:
    """Publish one runtime-originated message (``from: runtime``) at canonical HEAD; refs
    with rev ``@result`` (the default) are read at HEAD. ``result.seq`` is its number.
    Raises MessageProblems (nothing written) or PublishError."""
    recover(canonical)
    head = gitops.head(canonical)
    message = build_message(kind, title, body, refs, FROM_RUNTIME, **fields)
    seq = _next_seq(canonical)
    message = _published_form(message, seq, head)
    check_message(message, outbox=True)
    _write_outbox(canonical, message)
    _checkpoint("before_commit")
    _commit_pending(canonical, f"publish runtime {kind} {seq}")
    _checkpoint("before_push")
    return PublishResult(seqs=[seq], pushed=push(canonical, allow_no_remote))


def _acknowledge_inbound_tasks(canonical: Path, handoff: dict) -> None:
    """TODO(Task 3.7): when the handoff's card was created for an inbound ``task``, publish
    the runtime acknowledgement (a ``note`` with ``in_reply_to`` the I-id) and move the
    inbound to ``acknowledged`` (C11). Routing, which links a card to its inbound, does not
    exist yet."""
    del canonical, handoff


def publish_hook(
    canonical: Path, merge_sha: str, handoff: dict, allow_no_remote: bool = False
) -> PublishResult:
    """The ``publish`` hook for ``runtime.merge.process_handoffs``: called under the canonical
    lock after a merge; idempotent. ``process_handoffs`` ignores the return value, so a caller
    that needs ``pushed``/``skipped`` wraps this hook (queue-keeper, Task 3.9). Tests bind
    ``allow_no_remote`` with ``functools.partial``."""
    result = publish_staged(canonical, merge_sha, allow_no_remote)
    _acknowledge_inbound_tasks(canonical, handoff)
    return result
