"""Small helpers shared by the record modules: time, JSON columns, problems, display ids.

Kept generic on purpose: nothing here knows about one record kind. The record modules
(``goals.py``, ``tasks.py``, ...) build their validation and their JSON-ready results from these.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from research_core.store.errors import NotFound

if TYPE_CHECKING:
    from research_core.store.db import Store

# Display-id prefixes (design §B.2 and the overview's identifiers). The registry derives each
# RecordSpec.prefix from this map, so this is the one source.
PREFIXES: dict[str, str] = {
    "G": "goal",
    "T": "task",
    "V": "version",
    "C": "claim",
    "X": "experiment",
    "D": "directive",
    "I": "intent",
    "N": "notice",
}
KIND_PREFIXES: dict[str, str] = {kind: prefix for prefix, kind in PREFIXES.items()}

_DISPLAY_ID = re.compile(r"^([A-Z])-([1-9][0-9]*)$")
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

Clock = Callable[[], datetime]


def now_iso(clock: Clock | None = None) -> str:
    """The current time as ``2026-10-28T21:04:05.123456Z`` (UTC, microseconds).

    ``clock`` returns a datetime (aware or naive UTC); tests inject one. Microseconds keep
    ``created`` values orderable (design B.4).
    """
    moment = clock() if clock is not None else datetime.now(UTC)
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return moment.strftime(_ISO_FORMAT)


def parse_iso(text: str) -> datetime:
    """The inverse of ``now_iso``: an aware UTC datetime."""
    moment = datetime.fromisoformat(text)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def json_field(value: Any) -> str:
    """JSON text for a TEXT column: compact, sorted keys, so equal values store equal text."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_json(text: str | None, default: Any = None) -> Any:
    """The value of a JSON column; ``default`` when the column is NULL or empty."""
    if not text:
        return default
    return json.loads(text)


def require(problems: list[str], condition: object, message: str) -> bool:
    """Append ``message`` to ``problems`` unless ``condition`` holds; return whether it held.

    Record modules collect every problem this way and raise ``StoreError(problems)`` once, so a
    refusal reports all problems at once (design B.2).
    """
    if not condition:
        problems.append(message)
        return False
    return True


def word_count(text: str) -> int:
    return len(text.split())


def display_id(prefix: str, n: int) -> str:
    """``display_id("G", 3) == "G-3"``."""
    if prefix not in PREFIXES:
        raise ValueError(f"not a record prefix: {prefix!r}")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"not a record number: {n!r}")
    return f"{prefix}-{n}"


def display_id_for(kind: str, n: int) -> str:
    """``display_id_for("experiment", 5) == "X-5"``."""
    if kind not in KIND_PREFIXES:
        raise ValueError(f"records of kind {kind!r} have no display id")
    return display_id(KIND_PREFIXES[kind], n)


def parse_display_id(text: str) -> tuple[str, int]:
    """``parse_display_id("G-3") == ("goal", 3)``; refuses ``G-0``, ``g-3``, ``GX-1``."""
    match = _DISPLAY_ID.match(text)
    if match is None or match.group(1) not in PREFIXES:
        raise ValueError(f"not a record id: {text!r} (expected a form like G-3 or T-42)")
    prefix, number = match.groups()
    return PREFIXES[prefix], int(number)


def fetch_row(
    store: Store, table: str, row_id: object, *, display: str, id_column: str = "id"
) -> sqlite3.Row:
    """One row by id, or ``NotFound(["<display> does not exist"])``."""
    row = store.one(f"SELECT * FROM {table} WHERE {id_column} = ?", (row_id,))
    if row is None:
        raise NotFound([f"{display} does not exist"])
    return row
