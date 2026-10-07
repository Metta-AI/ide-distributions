"""The ``ar-comms/0`` message envelope (``schemas/message.json``; Overview C5, System Design 7).

Versioning is additive within ``ar-comms/0``: new optional fields and new kinds may appear, and
readers ignore what they do not know. Writers are stricter: ``message_problems`` also requires
the kind to be one of ``KINDS``, so this version never emits a kind its own readers skip.
"""

import datetime
import functools
import json
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from autoresearch.ids import new_id

SCHEMA_VERSION = "ar-comms/0"
KINDS = ("note", "highlight", "question", "help_request", "suggestion", "decision", "seen")
RESULT_REV = "@result"  # staged ref rev: replaced by the merge SHA at publication
FROM_AGENT = "agent"
FROM_RUNTIME = "runtime"
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "message.json"


class MessageProblems(ValueError):
    """A message does not satisfy the envelope; ``problems`` lists why."""

    def __init__(self, problems: list[str]):
        super().__init__("invalid message: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Ref:
    """``ar://<type>/<id>[#anchor]`` read at ``rev`` (a commit SHA, or ``@result`` when staged)."""

    uri: str
    rev: str = RESULT_REV
    role: str | None = None  # subject | evidence | context
    note: str | None = None

    def to_dict(self) -> dict:
        data = {"uri": self.uri, "rev": self.rev}
        if self.role is not None:
            data["role"] = self.role
        if self.note is not None:
            data["note"] = self.note
        return data


def ref_dict(ref: "Ref | dict") -> dict:
    """A ref as stored in a message; a dict without ``rev`` gets ``@result``."""
    if isinstance(ref, Ref):
        return ref.to_dict()
    return {"rev": RESULT_REV, **ref}


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def build_message(kind: str, title: str, body: str, refs: list, sender: str, **fields) -> dict:
    """A new message with a fresh ``M-`` id. ``fields`` adds optional envelope fields
    (``in_reply_to``, ``thread``, ``urgency``, or any additive field)."""
    return {
        **fields,
        "schema": SCHEMA_VERSION,
        "id": new_id("message"),
        "kind": kind,
        "title": title,
        "body": body,
        "refs": [ref_dict(ref) for ref in refs],
        "from": sender,
        "created_at": now_iso(),
    }


@functools.cache
def _validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def message_problems(message, outbox: bool = False) -> list[str]:
    """Problems of a message this version writes; ``outbox`` adds the published-form rules
    (``seq`` and ``published_rev`` present, no ``@result`` rev left)."""
    if not isinstance(message, dict):
        return ["a message must be a JSON object"]
    problems = []
    for error in sorted(_validator().iter_errors(message), key=str):
        location = "/".join(str(part) for part in error.absolute_path) or "(message)"
        problems.append(f"{location}: {error.message}")
    if isinstance(message.get("kind"), str) and message["kind"] not in KINDS:
        problems.append(f"kind: {message['kind']!r} is not one of {', '.join(KINDS)}")
    if outbox:
        for name in ("seq", "published_rev"):
            if name not in message:
                problems.append(f"{name}: required in a published message")
        refs = message.get("refs") if isinstance(message.get("refs"), list) else []
        for index, ref in enumerate(refs):
            if isinstance(ref, dict) and ref.get("rev") == RESULT_REV:
                problems.append(f"refs/{index}/rev: {RESULT_REV} left in a published message")
    return problems


def check_message(message: dict, outbox: bool = False) -> None:
    problems = message_problems(message, outbox=outbox)
    if problems:
        raise MessageProblems(problems)
