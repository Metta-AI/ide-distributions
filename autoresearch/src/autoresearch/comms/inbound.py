"""Inbound messages from external agents (C11, System Design 7 Inbound). Minimal form.

``submit_inbound`` validates the System Design 7 shape of each kind, writes
``comms/inbox/<I-id>.json`` in canonical and commits it; a ``seen`` also adds its message ids
to the author's durable seen record ``.supervisor/ledger/seen/<author>.json``
(``{consumer, ids}``) in the same commit. Task 3.7 adds ``schemas/inbound.json``, routing to
a responsible card, and delivery states in ``.supervisor/ledger/inbound/<I-id>.json``.

Every kind carries ``author`` (the consumer name; it carries the full authority of the human
it serves). Shapes (other fields are kept):

- ``reply``: ``in_reply_to`` (M-id) and ``choice`` and/or ``text``.
- ``steer``: ``text``, ``urgency`` (now | next | whenever), optional ``proposed_topic``
  ``{title, objective, metrics, priority}``.
- ``seen``: ``ids`` (published M-ids, at least one).
- ``task``: ``title``, ``mode``, ``why``, ``refs`` (ar:// strings), ``priority`` (P0-P5).
"""

import json
import re
from pathlib import Path

from autoresearch.comms.envelope import PRIORITIES, SCHEMA_VERSION, now_iso
from autoresearch.comms.publisher import RECEIPTS_DIR, commit_and_push
from autoresearch.comms.reader import check_consumer, seen_path
from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.paths import Layout
from autoresearch.runtime.ledger import canonical_lock

INBOUND_KINDS = ("reply", "steer", "task", "seen")
URGENCIES = ("now", "next", "whenever")
MODES = ("investigation", "scouting", "experimentation", "maintenance", "planning")
_MESSAGE_ID = re.compile(r"M-[0-9A-HJKMNP-TV-Z]{26}")


class InboundProblems(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid inbound message: " + "; ".join(problems))
        self.problems = problems


def _text(payload: dict, name: str) -> bool:
    return isinstance(payload.get(name), str) and bool(payload[name].strip())


def _required_text(payload: dict, names: tuple[str, ...], where: str = "") -> list[str]:
    return [f"{where}{name}: required text" for name in names if not _text(payload, name)]


def _is_published(canonical: Path, message_id: str) -> bool:
    return gitops.show(canonical, "HEAD", f"{RECEIPTS_DIR}/{message_id}.json") is not None


def _reply_problems(payload: dict) -> list[str]:
    problems = []
    if not isinstance(payload.get("in_reply_to"), str) or not _MESSAGE_ID.fullmatch(
        payload["in_reply_to"]
    ):
        problems.append("in_reply_to: a message id (M-...) is required")
    if not (_text(payload, "text") or _text(payload, "choice")):
        problems.append("choice or text: at least one is required")
    return problems


def _steer_problems(payload: dict) -> list[str]:
    problems = _required_text(payload, ("text",))
    if payload.get("urgency") not in URGENCIES:
        problems.append(f"urgency: must be one of {', '.join(URGENCIES)}")
    topic = payload.get("proposed_topic")
    if topic is None:
        return problems
    if not isinstance(topic, dict):
        return [*problems, "proposed_topic: must be an object"]
    problems += _required_text(topic, ("title", "objective"), "proposed_topic.")
    if not isinstance(topic.get("metrics"), list):
        problems.append("proposed_topic.metrics: must be a list")
    if topic.get("priority") not in PRIORITIES:
        problems.append(f"proposed_topic.priority: must be one of {', '.join(PRIORITIES)}")
    return problems


def _task_problems(payload: dict) -> list[str]:
    problems = _required_text(payload, ("title", "why"))
    if payload.get("mode") not in MODES:
        problems.append(f"mode: must be one of {', '.join(MODES)}")
    refs = payload.get("refs")
    if not isinstance(refs, list) or not all(
        isinstance(ref, str) and ref.startswith("ar://") for ref in refs
    ):
        problems.append("refs: a list of ar:// strings is required")
    if payload.get("priority") not in PRIORITIES:
        problems.append(f"priority: must be one of {', '.join(PRIORITIES)}")
    return problems


def _seen_problems(canonical: Path, payload: dict) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        return ["ids: a non-empty list of message ids is required"]
    return [f"ids: {i} is not a published message" for i in ids if not _is_published(canonical, i)]


def inbound_problems(canonical: Path, kind: str, payload: dict, author) -> list[str]:
    if kind not in INBOUND_KINDS:
        return [f"kind: {kind!r} is not one of {', '.join(INBOUND_KINDS)}"]
    problems = []
    if not isinstance(author, str):
        problems.append("author: required")
    else:
        try:
            check_consumer(author)
        except ValueError as error:
            problems.append(f"author: {error}")
    checks = {
        "reply": _reply_problems,
        "steer": _steer_problems,
        "task": _task_problems,
        "seen": lambda payload: _seen_problems(canonical, payload),
    }
    return problems + checks[kind](payload)


def _record_seen(canonical: Path, consumer: str, ids: list[str]) -> Path:
    path = seen_path(canonical, consumer)
    committed = gitops.show(canonical, "HEAD", path.relative_to(canonical).as_posix())
    record = json.loads(committed) if committed else {"consumer": consumer, "ids": []}
    record["ids"] = sorted({*record["ids"], *ids})
    write_json(path, record)
    return path


def submit_inbound(canonical: Path, kind: str, payload: dict, author: str) -> str:
    """Validate, write and commit one inbound message from ``author``; return its I-id.
    Takes the canonical lock: the caller must not hold it. Raises InboundProblems."""
    inbound_id = new_id("inbound")
    record = {
        **payload,
        "schema": SCHEMA_VERSION,
        "id": inbound_id,
        "kind": kind,
        "author": author,
        "received_at": now_iso(),
    }
    path = Layout(canonical).comms_inbox / f"{inbound_id}.json"
    with canonical_lock(canonical):
        problems = inbound_problems(canonical, kind, payload, author)
        if problems:
            raise InboundProblems(problems)
        write_json(path, record)
        paths = [path]
        if kind == "seen":
            paths.append(_record_seen(canonical, author, payload["ids"]))
        commit_and_push(canonical, paths, f"inbound {kind} {inbound_id}")
    return inbound_id
