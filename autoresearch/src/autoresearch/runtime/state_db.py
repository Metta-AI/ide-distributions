"""Reads of the profile's Hermes ``state.db`` and session pricing. STDLIB ONLY: the Hermes
plugin imports this (the coach chat's day cap, ``plugin.coach``); ``runtime.spend`` and
``runtime.baseline`` use it too.

Hermes records per-session token usage and its own cost estimate in ``sessions``
(``estimated_cost_usd``, ``actual_cost_usd``, updated after each API call) and the platform
that opened the session in ``source`` (``api_server`` for the IDE coach chat, which arrives
through Hermes's API server; ``cli`` for Kanban workers). ``session_usd`` prices one row: the
actual cost when Hermes has one, else its estimate, else (no estimate, tokens above 0) the
token counts at ``AUTORESEARCH_FALLBACK_PRICES``.

**Chat spend per day.** The chat is one long-lived session (the bridge sends one fixed
session id) and Hermes stores only its running total, so a day's chat spend is the rise of
each chat session's total since the day's first snapshot. ``chat_day_usd`` keeps the
snapshot in a small JSON file (``{"day": "YYYY-MM-DD", "base_usd": {session id: usd}}``,
UTC days): a chat session first seen on a day is based at 0 when it started that day, else at
its total when first seen. A lost or unreadable snapshot only under-counts the chat spend
before it was rewritten; the inference key's own limit stays the hard ceiling.
"""

import datetime
import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path

STATE_DB = "state.db"
CHAT_SOURCE = "api_server"  # sessions.source of the IDE coach chat
ENV_FALLBACK_PRICES = "AUTORESEARCH_FALLBACK_PRICES"
# USD per million tokens, for sessions Hermes could not price. Above the rate the box showed
# for kimi-k3 (about $36.5 for 4.6M input, 0.39M output, 59.2M cache-read tokens).
DEFAULT_FALLBACK_PRICES = {"input": 3.0, "output": 12.0, "cache_read": 0.75, "cache_write": 3.0}
_TOKEN_COLUMNS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_tokens",
    "cache_write": "cache_write_tokens",
}
# The columns ``session_usd`` reads, for ``SELECT {COLUMNS} FROM sessions s``.
COLUMNS = "s.id, s.actual_cost_usd, s.estimated_cost_usd, s.cost_status, " + ", ".join(
    f"s.{column}" for column in _TOKEN_COLUMNS.values()
)


def parse_prices(raw: str | None) -> dict[str, float]:
    """``input=3,output=12,cache_read=0.75,cache_write=3`` over the defaults. ValueError for
    an unknown kind or a value that is not a number."""
    prices = dict(DEFAULT_FALLBACK_PRICES)
    for entry in (raw or "").split(","):
        if not entry.strip():
            continue
        kind, _, value = (part.strip() for part in entry.partition("="))
        if kind not in prices:
            raise ValueError(f"{ENV_FALLBACK_PRICES}: unknown token kind {kind!r}")
        try:
            prices[kind] = float(value)
        except ValueError:
            raise ValueError(f"{ENV_FALLBACK_PRICES}: {kind}: not a number: {value!r}") from None
    return prices


def session_usd(row: sqlite3.Row, prices: Mapping[str, float]) -> tuple[float, bool]:
    """(cost, priced from tokens?) of one session row (module docstring)."""
    actual = row["actual_cost_usd"] or 0.0
    if actual > 0:
        return actual, False
    estimated = row["estimated_cost_usd"] or 0.0
    if estimated > 0 or row["cost_status"] == "included":
        return estimated, False
    tokens = {kind: row[column] or 0 for kind, column in _TOKEN_COLUMNS.items()}
    if not any(tokens.values()):
        return 0.0, False
    return sum(prices[kind] * count for kind, count in tokens.items()) / 1_000_000, True


def state_rows(hermes_home: Path, query: str, params: tuple) -> list[sqlite3.Row]:
    """Rows of a read-only query on the profile ``state.db``; none before the first session."""
    path = Path(hermes_home) / STATE_DB
    if not path.exists():
        return []
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(query, params))
    finally:
        connection.close()


def utc_day_start(now: datetime.datetime) -> datetime.datetime:
    return now.astimezone(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _read_snapshot(path: Path, day: str) -> tuple[dict[str, float], bool]:
    """(base costs for ``day``, readable and current?)."""
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("day") == day and isinstance(snapshot.get("base_usd"), dict):
            return {str(k): float(v) for k, v in snapshot["base_usd"].items()}, True
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}, False


def _write_snapshot(path: Path, day: str, base: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps({"day": day, "base_usd": base}, sort_keys=True))
    os.replace(temporary, path)


def chat_day_usd(
    hermes_home: Path, snapshot_path: Path, now: datetime.datetime, prices: Mapping[str, float]
) -> float:
    """The chat sessions' spend on ``now``'s UTC day (module docstring), updating the
    snapshot for sessions it had not seen today. One writer at a time: the caller serializes
    calls (the plugin holds a lock). Raises sqlite3.Error when ``state.db`` cannot be read."""
    start = utc_day_start(now)
    day = f"{start:%Y-%m-%d}"
    rows = state_rows(
        hermes_home,
        f"SELECT {COLUMNS}, s.started_at FROM sessions s WHERE s.source = ?",
        (CHAT_SOURCE,),
    )
    base, current = _read_snapshot(snapshot_path, day)
    changed = not current
    total = 0.0
    for row in rows:
        usd = session_usd(row, prices)[0]
        if row["id"] not in base:
            base[row["id"]] = 0.0 if (row["started_at"] or 0) >= start.timestamp() else usd
            changed = True
        total += max(0.0, usd - base[row["id"]])
    if changed:
        _write_snapshot(snapshot_path, day, base)
    return total
