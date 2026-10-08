"""Inbound delivery records ``.supervisor/ledger/inbound/<I-id>.json`` (Overview C11;
``schemas/inbound.json``). Plain file I/O shared by routing (``runtime.routing``), admission
and the publish hook; it imports nothing that imports it back.

A record moves ``received -> routed -> acknowledged``. ``responsible`` names the one card
that must acknowledge it: ``request_id`` (the card request it was routed to), ``task_id``
(filled once that request is admitted, or when a planning card takes a backlog entry), and
``backlog`` (routed to the planning backlog). Writers hold the canonical lock and commit
what they write; readers use the committed records (git is the ledger's truth).
"""

import functools
import json
from pathlib import Path

import jsonschema

from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.runtime.ledger import now_iso

INBOUND_DIR = ".supervisor/ledger/inbound"
BACKLOG_DIR = ".supervisor/ledger/backlog"
INBOX_DIR = "comms/inbox"
_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "inbound.json"


@functools.cache
def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def delivery_path(canonical: Path, inbound_id: str) -> Path:
    return Path(canonical) / INBOUND_DIR / f"{inbound_id}.json"


def backlog_path(canonical: Path, inbound_id: str) -> Path:
    return Path(canonical) / BACKLOG_DIR / f"{inbound_id}.json"


def _committed_dir(canonical: Path, directory: str) -> dict[str, dict]:
    """Stem -> JSON of every ``*.json`` committed at HEAD under ``directory``."""
    listing = gitops.git(canonical, "ls-tree", "--name-only", "HEAD", f"{directory}/")
    found = {}
    for path in sorted(listing.splitlines()):
        if path.endswith(".json"):
            found[path.rsplit("/", 1)[-1].removesuffix(".json")] = json.loads(
                gitops.show(canonical, "HEAD", path)
            )
    return found


def inbox(canonical: Path) -> dict[str, dict]:
    """Inbound id -> inbound message, as committed at HEAD, sorted by id. A message an
    external agent wrote directly has no ``received_at``; its ``created`` stands for it."""
    messages = _committed_dir(canonical, INBOX_DIR)
    for message in messages.values():
        message.setdefault("received_at", message.get("created"))
    return messages


def records(canonical: Path) -> dict[str, dict]:
    """I-id -> committed delivery record."""
    return _committed_dir(canonical, INBOUND_DIR)


def backlog_entries(canonical: Path) -> dict[str, dict]:
    return _committed_dir(canonical, BACKLOG_DIR)


def new_record(message: dict, late: bool, thread: str | None) -> dict:
    record = {
        "id": message["id"],
        "kind": message["kind"],
        "received_at": message["received_at"],
        "state": "received",
        "responsible": {"task_id": None, "request_id": None, "backlog": False},
        "acknowledged_by": None,
        "late": late,
        "history": [{"state": "received", "at": now_iso()}],
    }
    if thread is not None:
        record["thread"] = thread
    return record


def moved(record: dict, state: str, note: str | None = None, **fields) -> dict:
    """``record`` in ``state`` with ``fields`` replaced and a history entry added."""
    entry = {"state": state, "at": now_iso()}
    if note:
        entry["note"] = note
    return record | fields | {"state": state, "history": [*record["history"], entry]}


def write_record(canonical: Path, record: dict) -> Path:
    """Validate and write one delivery record; the caller commits the returned path."""
    jsonschema.validate(record, _schema())
    path = delivery_path(canonical, record["id"])
    write_json(path, record)
    return path


def responsible_ids(
    canonical: Path, task_id: str, request_id: str | None = None, planning: bool = False
) -> list[str]:
    """Unacknowledged inbound this card must acknowledge: routed to ``task_id`` or to
    ``request_id``, plus, for a planning card, backlog entries no card has taken yet."""
    found = []
    for inbound_id, record in records(canonical).items():
        if record["state"] != "routed":
            continue
        responsible = record["responsible"]
        unbound_backlog = responsible["backlog"] and responsible["task_id"] is None
        if (
            responsible["task_id"] == task_id
            or (request_id is not None and responsible["request_id"] == request_id)
            or (planning and unbound_backlog)
        ):
            found.append(inbound_id)
    return found
