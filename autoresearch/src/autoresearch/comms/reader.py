"""Outbox reader: cursor reads, lookups and publication status (Runtime 9.2).

Readers see only outbox files committed at canonical HEAD, so a publication a crash left
half done is never visible. Each consumer keeps its own cursor (the last seq it processed) in
``.supervisor/runtime/cursors/<consumer>.json``; cursors are disposable (C2), and a consumer
that loses its cursor just reads again from the start (delivery is at-least-once and ids make
re-reads idempotent). Readers ignore unknown fields (they are kept in the returned dicts) and,
by default, messages of unknown kinds.

Per-consumer ``seen`` records are durable: ``.supervisor/ledger/seen/<consumer>.json``
(``{consumer, seqs}``), written by an inbound ``seen`` (``comms.inbound``).
"""

import json
import re
from pathlib import Path

from autoresearch.comms.envelope import KINDS
from autoresearch.comms.publisher import OUTBOX_DIR, OUTBOX_NAME, RECEIPTS_DIR
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json

CURSORS_DIR = ".supervisor/runtime/cursors"
SEEN_DIR = ".supervisor/ledger/seen"
CONSUMER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def check_consumer(consumer: str) -> str:
    if not CONSUMER_NAME.fullmatch(consumer) or ".." in consumer:
        raise ValueError(f"invalid consumer name {consumer!r}: use letters, digits, '.', '_', '-'")
    return consumer


def _outbox_index(canonical: Path) -> list[tuple[int, str, str]]:
    """(seq, message id, path) of every outbox file at HEAD, by seq."""
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{OUTBOX_DIR}/")
    index = []
    for path in listing.splitlines():
        match = OUTBOX_NAME.fullmatch(path.rsplit("/", 1)[-1])
        if match:
            index.append((int(match["seq"]), match["id"], path))
    return sorted(index)


def _load(canonical: Path, path: str) -> dict:
    return json.loads(gitops.show(canonical, "HEAD", path))


def last_seq(canonical: Path) -> int:
    """Highest published seq at HEAD (0 if none), counting messages of unknown kinds."""
    index = _outbox_index(canonical)
    return index[-1][0] if index else 0


def cursor_path(canonical: Path, consumer: str) -> Path:
    return Path(canonical) / CURSORS_DIR / f"{check_consumer(consumer)}.json"


def cursor(canonical: Path, consumer: str) -> int:
    path = cursor_path(canonical, consumer)
    return read_json(path)["seq"] if path.exists() else 0


def advance(canonical: Path, consumer: str, seq: int) -> None:
    """Move ``consumer``'s cursor to ``seq``; it never moves backwards."""
    if seq > cursor(canonical, consumer):
        write_json(cursor_path(canonical, consumer), {"consumer": consumer, "seq": seq})


def read_since(
    canonical: Path, consumer: str, after: int | None = None, include_unknown: bool = False
) -> list[dict]:
    """Published messages with seq > ``after`` (default: ``consumer``'s cursor), by seq.
    Does not move the cursor: call ``advance`` once the messages are handled."""
    start = cursor(canonical, consumer) if after is None else after
    messages = [_load(canonical, path) for seq, _, path in _outbox_index(canonical) if seq > start]
    if include_unknown:
        return messages
    return [message for message in messages if message.get("kind") in KINDS]


def get(canonical: Path, key: str | int) -> dict:
    """One published message by seq (int) or by M-id; LookupError if there is none."""
    for seq, message_id, path in _outbox_index(canonical):
        if key in (seq, message_id):
            return _load(canonical, path)
    raise LookupError(f"no published message {key!r}")


def seen_path(canonical: Path, consumer: str) -> Path:
    return Path(canonical) / SEEN_DIR / f"{check_consumer(consumer)}.json"


def status(canonical: Path, message_id: str) -> dict:
    """``{id, published, seq, published_rev, seen_by}`` for a message id, from the committed
    receipt and the per-consumer seen records."""
    content = gitops.show(canonical, "HEAD", f"{RECEIPTS_DIR}/{message_id}.json")
    if content is None:
        return {
            "id": message_id,
            "published": False,
            "seq": None,
            "published_rev": None,
            "seen_by": [],
        }
    receipt = json.loads(content)
    seen_by = []
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{SEEN_DIR}/")
    for path in sorted(listing.splitlines()):
        record = _load(canonical, path)
        if receipt["seq"] in record.get("seqs", []):
            seen_by.append(record["consumer"])
    return {
        "id": message_id,
        "published": True,
        "seq": receipt["seq"],
        "published_rev": receipt.get("published_rev"),
        "seen_by": seen_by,
    }
