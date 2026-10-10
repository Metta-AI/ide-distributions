"""Inbound messages from external agents (C11, System Design 7 Inbound).

``submit_inbound`` validates the System Design 7 shape of each kind, writes
``comms/inbox/<I-id>.json`` in canonical and commits it; a ``seen`` also adds its message ids
to the author's durable seen record ``.supervisor/ledger/seen/<author>.json``
(``{consumer, ids}``) in the same commit. Routing each message to its one responsible card
and its delivery state (``.supervisor/ledger/inbound/<I-id>.json``, ``schemas/inbound.json``)
belong to the runtime: ``runtime.routing`` and ``comms.delivery``.

``adopt_direct_writes`` takes the other path into the inbox (Runtime 9.2, the L0 file
adapter): an external agent such as the Softmax IDE bridge writes
``comms/inbox/<id>.json`` itself, with its own id, and never commits. queue-keeper's routing
calls it under the canonical lock: a valid file is committed unchanged (the external agent
owns it and may look for it again), an invalid one is left as it is, answered once with a
runtime note, and its id recorded as rejected for good. Only regular files are taken, and
when ``AUTORESEARCH_INBOX_WRITERS`` is set only files owned by those users: an inbound
message carries the human's authority (C11), and the OS user is the boundary between the
external agent and the research agent's own workers. An inbound id is any ``INBOUND_ID``
(the ``I-`` ULIDs that ``submit_inbound`` makes, or an external one such as
``<player id>:task:<key>``), except ones shaped like an outbound key (``M-...``, digits). A
direct write carries its own ``created`` time; the runtime's receipt time is the delivery
record's ``received_at``.

``derived_status`` gives the status of an outbound message (System Design 7). It is never
stored; it is derived from the inbox and the outbox.

Every kind carries ``author`` (the consumer name; it carries the full authority of the human
it serves). The kinds and their shapes are the registry's (``comms.kinds``; one JSON Schema per
kind in ``schemas/comms/inbound/``); other fields are kept. ``inbound_problems`` adds the
checks that need the workspace, named by a kind's schema and implemented here in ``CHECKS``
(``published_ids``: a seen's ids must be published messages).
"""

import datetime
import json
import os
import pwd
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.comms import delivery, kinds
from autoresearch.comms.envelope import FROM_RUNTIME, SCHEMA_VERSION, now_iso
from autoresearch.comms.publisher import RECEIPTS_DIR, commit_and_push, find_reply, push
from autoresearch.comms.reader import read_since, seen_path
from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.paths import Layout
from autoresearch.runtime import provenance
from autoresearch.runtime.ledger import canonical_lock

INBOUND_KINDS = kinds.inbound_kinds()
# One safe path component; the inbound envelope's "id", schemas/inbound.json "id" and the
# outbound envelope's "in_reply_to" match it.
INBOUND_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def is_inbound_id(text) -> bool:
    return isinstance(text, str) and bool(INBOUND_ID.fullmatch(text)) and ".." not in text


class InboundProblems(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid inbound message: " + "; ".join(problems))
        self.problems = problems


def _unpublished_ids(canonical: Path, message: dict) -> list[str]:
    return [
        f"ids: {message_id} is not a published message"
        for message_id in message["ids"]
        if gitops.show(canonical, "HEAD", f"{RECEIPTS_DIR}/{message_id}.json") is None
    ]


# The workspace checks a kind's schema names in ``x-autoresearch.checks``; each runs only on a
# message that passed its schema.
CHECKS = {"published_ids": _unpublished_ids}


def inbound_problems(canonical: Path, message) -> list[str]:
    """Problems of an inbound message: its kind's schema (``comms.kinds``), then the
    workspace checks the kind names."""
    problems = kinds.problems("inbound", message)
    if problems:
        return problems
    for check in kinds.get("inbound", message["kind"]).checks:
        problems += CHECKS[check](canonical, message)
    return problems


def _record_seen(canonical: Path, consumer: str, ids: list[str]) -> Path:
    path = seen_path(canonical, consumer)
    committed = gitops.show(canonical, "HEAD", path.relative_to(canonical).as_posix())
    record = json.loads(committed) if committed else {"consumer": consumer, "ids": []}
    record["ids"] = sorted({*record["ids"], *ids})
    write_json(path, record)
    return path


def _inbound_record(kind: str, payload: dict, author: str, inbound_id: str) -> dict:
    return {
        **payload,
        "schema": SCHEMA_VERSION,
        "id": inbound_id,
        "kind": kind,
        "author": author,
        "received_at": now_iso(),
    }


def check_inbound(canonical: Path, kind: str, payload: dict, author: str) -> None:
    """Validate the message ``submit_inbound`` would write, without writing it (``comms
    inbound --dry-run``, which the coach chat's ``propose_inbound`` runs). Raises
    InboundProblems."""
    problems = inbound_problems(canonical, _inbound_record(kind, payload, author, new_id("inbound")))
    if problems:
        raise InboundProblems(problems)


def submit_inbound(canonical: Path, kind: str, payload: dict, author: str) -> str:
    """Validate, write and commit one inbound message from ``author``; return its I-id.
    Takes the canonical lock: the caller must not hold it. Raises InboundProblems."""
    inbound_id = new_id("inbound")
    record = _inbound_record(kind, payload, author, inbound_id)
    path = Layout(canonical).comms_inbox / f"{inbound_id}.json"
    with canonical_lock(canonical):
        problems = inbound_problems(canonical, record)
        if problems:
            raise InboundProblems(problems)
        write_json(path, record)
        paths = [path]
        if kind == "seen":
            paths.append(_record_seen(canonical, author, payload["ids"]))
        commit_and_push(canonical, paths, f"inbound {kind} {inbound_id}")
    return inbound_id


INBOX_WRITERS_ENV = "AUTORESEARCH_INBOX_WRITERS"
# A direct write that is still not JSON after this long is rejected (a writer that renames
# into place never leaves one; a partial file younger than this is retried next run).
UNPARSEABLE_AFTER_S = 300


def inbox_writers(value: str | None) -> frozenset[int] | None:
    """``AUTORESEARCH_INBOX_WRITERS`` (comma-separated uids or user names) as uids; None when
    unset. An unknown user name raises ValueError."""
    if not value or not value.strip():
        return None
    uids = set()
    for item in (part.strip() for part in value.split(",")):
        if not item:
            continue
        if item.isdigit():
            uids.add(int(item))
            continue
        try:
            uids.add(pwd.getpwnam(item).pw_uid)
        except KeyError:
            raise ValueError(f"{INBOX_WRITERS_ENV}: unknown user {item!r}") from None
    return frozenset(uids)


@dataclass
class Adoption:
    """What ``adopt_direct_writes`` did: committed ids, and new rejections (id -> problems)
    that routing answers with a runtime note and records in the rejection ledger."""

    adopted: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)


def _direct_write_problems(canonical: Path, name: str, message) -> list[str]:
    if not isinstance(message, dict):
        return ["the file is not a JSON object"]
    if message.get("id") != name:
        return [f"id: must equal the file name {name!r}"]
    received = message.get("received_at") or message.get("created")
    try:
        parse_time(received)
    except (TypeError, ValueError):
        return ["created: an ISO 8601 time is required"]
    return inbound_problems(canonical, message)


def _read_regular(path: Path) -> bytes:
    """The bytes of ``path`` without following a symlink planted after the lstat."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _candidate_problems(
    canonical: Path, path: Path, name: str, writers: frozenset[int] | None, now: float
) -> tuple[list[str] | None, bytes | None]:
    """``(problems, data)`` of one untracked inbox file: ``([], data)`` to adopt it,
    ``(problems, None)`` to reject it, ``(None, None)`` to leave it for a later run."""
    problems, data = _file_problems(canonical, path, name, writers, now)
    # A runtime reply with no rejection record: a crash between the note and the record.
    # The id stays rejected; keep the real problems when the file still shows them.
    if find_reply(canonical, name, FROM_RUNTIME) is not None:
        return problems or ["already answered by the runtime"], None
    return problems, data


def _file_problems(
    canonical: Path, path: Path, name: str, writers: frozenset[int] | None, now: float
) -> tuple[list[str] | None, bytes | None]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(info.st_mode):
        return ["not a regular file (a symlink or another file type)"], None
    if writers is not None and info.st_uid not in writers:
        return [f"owner uid {info.st_uid} is not an allowed inbox writer ({INBOX_WRITERS_ENV})"], None
    try:
        data = _read_regular(path)
        message = json.loads(data)
    except (OSError, ValueError):
        if now - info.st_mtime > UNPARSEABLE_AFTER_S:
            return ["the file is not valid JSON"], None
        return None, None  # perhaps still being written
    problems = _direct_write_problems(canonical, name, message)
    return (problems, None) if problems else ([], data)


def adopt_direct_writes(
    canonical: Path, writers: frozenset[int] | None = None, now: float | None = None
) -> Adoption:
    """Commit the inbox files an external agent wrote directly (module docstring); call
    under the canonical lock. Only regular files, owned by one of ``writers`` when given
    (``inbox_writers``), are adopted, with the exact bytes that were validated. An id in the
    rejection ledger (``delivery.rejected_ids``) is final and never looked at again; a file
    whose name is not an inbound id is ignored."""
    now = time.time() if now is None else now
    listing = gitops.git(canonical, "ls-files", "--others", "-z", "--", delivery.INBOX_DIR)
    final = delivery.rejected_ids(canonical)
    result = Adoption()
    blobs: dict[str, bytes] = {}
    seen_ids: dict[str, set[str]] = {}
    for relative in sorted(entry for entry in listing.split("\0") if entry.endswith(".json")):
        parent, _, file_name = relative.rpartition("/")
        name = file_name.removesuffix(".json")
        if parent != delivery.INBOX_DIR or not is_inbound_id(name) or name in final:
            continue
        problems, data = _candidate_problems(canonical, Path(canonical) / relative, name, writers, now)
        if problems is None:
            continue
        if problems:
            result.rejected[name] = problems
            continue
        result.adopted.append(name)
        blobs[relative] = data
        message = json.loads(data)
        if message["kind"] == "seen":
            seen_ids.setdefault(message["author"], set()).update(message["ids"])
    if blobs:
        paths = [
            _record_seen(canonical, author, sorted(ids)).relative_to(canonical).as_posix()
            for author, ids in sorted(seen_ids.items())
        ]
        subject = f"inbound: adopted {len(blobs)} direct writes"
        provenance.commit_recorded(
            canonical, subject, lambda: gitops.prepare_blobs(canonical, blobs, paths, subject)
        )
        push(canonical, allow_no_remote=True)
    return result


# --- derived status of outbound messages ---


@dataclass(frozen=True)
class StatusInfo:
    """``state``: open | answered | withdrawn | expired. ``late``: a reply arrived after the
    message's ``expires``."""

    state: str
    late: bool


def parse_time(text: str) -> datetime.datetime:
    """An ISO 8601 time; a time without a zone is taken as UTC."""
    parsed = datetime.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


def is_late(message: dict, received_at: str) -> bool:
    """True if an inbound received at ``received_at`` answers ``message`` after it expired."""
    expires = message.get("expires")
    return bool(expires) and parse_time(received_at) > parse_time(expires)


def derived_status(
    canonical: Path, message_id: str, now: datetime.datetime | None = None
) -> StatusInfo:
    """Status of a published outbound message at canonical HEAD: ``answered`` if an inbound
    reply has ``in_reply_to`` == its id; else ``withdrawn`` if a later message supersedes
    it; else ``expired`` if its ``expires`` has passed; else ``open``. Raises LookupError
    for an unpublished id."""
    published = read_since(canonical, "status", after=0, include_unknown=True)
    by_id = {message["id"]: message for message in published}
    if message_id not in by_id:
        raise LookupError(f"no published message {message_id!r}")
    message = by_id[message_id]
    replies = [
        inbound
        for inbound in delivery.inbox(canonical).values()
        if inbound.get("kind") == "reply" and inbound.get("in_reply_to") == message_id
    ]
    late = any(is_late(message, reply["received_at"]) for reply in replies)
    if replies:
        return StatusInfo("answered", late)
    if any(other.get("supersedes") == message_id for other in published):
        return StatusInfo("withdrawn", late)
    now = now or datetime.datetime.now(datetime.UTC)
    if message.get("expires") and parse_time(message["expires"]) <= now:
        return StatusInfo("expired", late)
    return StatusInfo("open", late)
