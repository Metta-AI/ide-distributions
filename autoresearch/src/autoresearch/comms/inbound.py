"""Inbound messages from external agents (C11, System Design 7 Inbound). Minimal form.

``submit_inbound`` validates a minimal shape per kind, writes ``comms/inbox/<I-id>.json`` in
canonical and commits it; a ``seen`` also adds its seq to the consumer's durable seen record
``.supervisor/ledger/seen/<consumer>.json`` in the same commit. Task 3.7 adds
``schemas/inbound.json``, routing to a responsible card, and delivery states in
``.supervisor/ledger/inbound/<I-id>.json``.

Minimal shapes (other fields are kept):

- ``reply``: ``in_reply_to`` (M-id) and ``text`` and/or ``choice``.
- ``steer``: ``text`` and ``urgency`` (now | next | whenever).
- ``task``: ``title``, ``mode``, ``why``; optional ``refs`` (list of strings) and ``priority``.
- ``seen``: ``consumer`` and ``seq`` (the published message seen).
"""

import re
from pathlib import Path

from autoresearch.comms.envelope import SCHEMA_VERSION, now_iso
from autoresearch.comms.publisher import commit_and_push
from autoresearch.comms.reader import check_consumer, seen_path
from autoresearch.ids import new_id
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.runtime.ledger import canonical_lock

INBOUND_KINDS = ("reply", "steer", "task", "seen")
URGENCIES = ("now", "next", "whenever")
_MESSAGE_ID = re.compile(r"M-[0-9A-HJKMNP-TV-Z]{26}")


class InboundProblems(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid inbound message: " + "; ".join(problems))
        self.problems = problems


def _text(payload: dict, name: str) -> bool:
    return isinstance(payload.get(name), str) and bool(payload[name].strip())


def inbound_problems(kind: str, payload: dict) -> list[str]:
    if kind not in INBOUND_KINDS:
        return [f"kind: {kind!r} is not one of {', '.join(INBOUND_KINDS)}"]
    problems = []
    if kind == "reply":
        if not isinstance(payload.get("in_reply_to"), str) or not _MESSAGE_ID.fullmatch(
            payload["in_reply_to"]
        ):
            problems.append("in_reply_to: a published message id (M-...) is required")
        if not (_text(payload, "text") or _text(payload, "choice")):
            problems.append("text or choice: required")
    elif kind == "steer":
        if not _text(payload, "text"):
            problems.append("text: required")
        if payload.get("urgency") not in URGENCIES:
            problems.append(f"urgency: must be one of {', '.join(URGENCIES)}")
    elif kind == "task":
        problems += [
            f"{name}: required" for name in ("title", "mode", "why") if not _text(payload, name)
        ]
        refs = payload.get("refs", [])
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            problems.append("refs: must be a list of ar:// strings")
    else:  # seen
        if not isinstance(payload.get("consumer"), str):
            problems.append("consumer: required")
        else:
            try:
                check_consumer(payload["consumer"])
            except ValueError as error:
                problems.append(f"consumer: {error}")
        seq = payload.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            problems.append("seq: a positive integer is required")
    return problems


def _record_seen(canonical: Path, consumer: str, seq: int) -> Path:
    path = seen_path(canonical, consumer)
    record = read_json(path) if path.exists() else {"consumer": consumer, "seqs": []}
    record["seqs"] = sorted({*record["seqs"], seq})
    write_json(path, record)
    return path


def submit_inbound(canonical: Path, kind: str, payload: dict, author: str | None = None) -> str:
    """Validate, write and commit one inbound message; return its I-id. Takes the canonical
    lock: the caller must not hold it. Raises InboundProblems."""
    problems = inbound_problems(kind, payload)
    if problems:
        raise InboundProblems(problems)
    inbound_id = new_id("inbound")
    record = {
        **payload,
        "schema": SCHEMA_VERSION,
        "id": inbound_id,
        "kind": kind,
        "received_at": now_iso(),
    }
    if author is not None:
        record["author"] = author
    path = Layout(canonical).comms_inbox / f"{inbound_id}.json"
    with canonical_lock(canonical):
        write_json(path, record)
        paths = [path]
        if kind == "seen":
            paths.append(_record_seen(canonical, payload["consumer"], payload["seq"]))
        commit_and_push(canonical, paths, f"inbound {kind} {inbound_id}")
    return inbound_id
