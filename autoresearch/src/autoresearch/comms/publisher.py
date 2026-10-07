"""Crash-safe outbox publication (Overview C5). Runtime only.

Files (all in canonical):

- ``comms/staged/<M-id>.json``: staged by a card worker; arrives in canonical with the merge.
- ``comms/outbox/<seq:08d>-<M-id>.json``: the published, immutable message.
- ``.supervisor/ledger/published/<M-id>.json``: the publication receipt
  ``{id, seq, file, published_rev, from, published_at}``.

Sequence numbers. ``next_seq = 1 + max(seq of the files in comms/outbox/)``: no counter, only
the outbox files themselves. Publishing one message writes, in this order, the outbox file,
the receipt, and (for a staged message) the removal of the staged file; one commit then
covers every message of the call, and a push of ``main`` to ``origin`` follows when that
remote exists. A failed push keeps the local commit; every later publication pushes again.

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
from pathlib import Path

from autoresearch.comms.envelope import (
    FROM_AGENT,
    FROM_RUNTIME,
    RESULT_REV,
    SCHEMA_VERSION,
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


class PublicationError(RuntimeError):
    """Publication state that recovery cannot repair (a receipt whose message is lost)."""


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
    fields = [field for field in output.split("\0") if field]
    paths, skip = [], False
    for field in fields:
        if skip:  # the source path of a rename/copy entry
            skip = False
            continue
        paths.append(field[3:])
        skip = field[0] in "RC"
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


def push(canonical: Path) -> bool:
    """Push ``main`` to ``origin`` if that remote exists; False only if the push failed."""
    if REMOTE not in gitops.git(canonical, "remote").split():
        return True
    return gitops.push(canonical, REMOTE, [BRANCH])


def commit_and_push(canonical: Path, paths: list[Path], message: str) -> str | None:
    """Commit exactly ``paths`` (absolute, under canonical) and push; for other comms writes
    (inbox, seen ledger). Call under the canonical lock."""
    relative = [Path(path).relative_to(canonical).as_posix() for path in paths]
    sha = gitops.commit_paths(canonical, relative, message)
    push(canonical)
    return sha


# --- recovery and publication ---


def _rebuild_outbox_file(canonical: Path, message_id: str, receipt: dict) -> None:
    content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
    staged = _staged_path(canonical, message_id)
    if content is None and staged.exists():
        content = staged.read_bytes()
    if content is None:
        raise PublicationError(
            f"receipt {receipt_path(canonical, message_id)} has no outbox file and no staged "
            "message to rebuild it from"
        )
    message = _published_form(
        _normalized_staged(json.loads(content)), receipt["seq"], receipt["published_rev"]
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


def _normalized_staged(message: dict) -> dict:
    """A staged message with the envelope fields the runtime owns: ``from`` is always
    ``agent`` (a worker cannot publish as the runtime); ``schema``/``created_at`` default."""
    return {"schema": SCHEMA_VERSION, "created_at": now_iso(), **message, "from": FROM_AGENT}


def publish_staged(canonical: Path, merge_sha: str) -> list[int]:
    """Publish every staged message committed in canonical at ``merge_sha``'s merge; return
    the new sequence numbers. Idempotent: a rerun publishes nothing twice and pushes again.

    Raises ``MessageProblems`` for a staged message that fails the envelope (handoff
    validation refuses those, so this means a message bypassed it)."""
    recover(canonical)
    receipts = _receipts(canonical)
    seqs = []
    next_seq = _next_seq(canonical)
    for message_id in _staged_at_head(canonical):
        if message_id in receipts:  # recover already removed it
            continue
        content = gitops.show(canonical, "HEAD", f"{STAGED_DIR}/{message_id}.json")
        message = _published_form(_normalized_staged(json.loads(content)), next_seq, merge_sha)
        _write_outbox(canonical, message)
        seqs.append(next_seq)
        next_seq += 1
    _checkpoint("before_commit")
    _commit_pending(canonical, f"publish {len(seqs)} message(s) of merge {merge_sha[:12]}")
    _checkpoint("before_push")
    push(canonical)
    return seqs


def publish_runtime(canonical: Path, kind: str, title: str, body: str, refs: list, **fields) -> int:
    """Publish one runtime-originated message (``from: runtime``) at canonical HEAD; refs
    with rev ``@result`` (the default) are read at HEAD. Returns its sequence number."""
    recover(canonical)
    head = gitops.head(canonical)
    message = build_message(kind, title, body, refs, FROM_RUNTIME, **fields)
    seq = _next_seq(canonical)
    _write_outbox(canonical, _published_form(message, seq, head))
    _checkpoint("before_commit")
    _commit_pending(canonical, f"publish runtime {kind} {seq}")
    _checkpoint("before_push")
    push(canonical)
    return seq


def _acknowledge_inbound_tasks(canonical: Path, handoff: dict) -> None:
    """TODO(Task 3.7): when the handoff's card was created for an inbound ``task``, publish
    the runtime acknowledgement (a ``note`` with ``in_reply_to`` the I-id) and move the
    inbound to ``acknowledged`` (C11). Routing, which links a card to its inbound, does not
    exist yet."""
    del canonical, handoff


def publish_hook(canonical: Path, merge_sha: str, handoff: dict) -> None:
    """The ``publish`` hook for ``runtime.merge.process_handoffs``: called under the canonical
    lock after a merge; idempotent."""
    publish_staged(canonical, merge_sha)
    _acknowledge_inbound_tasks(canonical, handoff)
