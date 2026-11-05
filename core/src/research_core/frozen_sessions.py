"""Bring open chats onto a release's tools and instructions.

Hermes stores what a session first sent and resends those bytes on every later turn, so the
provider's prompt cache stays valid: the tool definitions in ``sessions.tool_names`` and the
system prompt in ``sessions.system_prompt_hash``. It replaces stored tool definitions only when
``tool_pin_version()`` changes, and that is Hermes's own version, so a release of this
distribution never reaches an open chat by itself. A chat could keep a ``task`` schema that the
deployed validators refuse.

Preparation therefore compares a hash of what this distribution freezes into a session with the
hash recorded in the profile's ``state.db``. When they differ it asks every session to rebuild
both, once. A release that changes neither keeps every session's cache. Preparation runs before
the gateway on every boot, so a workspace that slept through several releases catches up on its
first turn, and no agent built from the old release is still in memory.

What this depends on in Hermes (checked by ``core/tests/hermes/test_frozen_sessions_real.py``):

- ``agent.conversation_loop._restore_pinned_tools`` treats ``tool_names = NULL`` as "no usable
  pin": the turn sends the current tools and stores them as the new pin.
- ``hermes sessions refresh-prompts`` makes each session rebuild its system prompt on its next
  turn. It is a supported command, so only the ``tool_names`` column is touched directly.
- ``state_meta(key, value)`` keeps keys Hermes does not know. Losing the row costs one more
  refresh, nothing else. The record lives there, not in a file, so that restoring ``state.db``
  from a backup also restores which release its sessions were refreshed for.

A failed refresh must not stop a workspace from starting: the chats stay as they were and the next
boot tries again, because the hash is recorded only in the transaction that clears the pins.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from research_core.tools.catalog import tools_for

SURFACE_KEY = "ide_distribution.agent_surface"
STATE_DB = "state.db"
# The plugin code that writes the two prompt sections into the frozen system prompt.
PROMPT_SECTION_MODULES = ("plugin/briefing.py", "plugin/session_context.py")


def agent_surface(source: Path) -> str:
    """A hash of what the distribution folder ``source`` freezes into its sessions.

    It covers the role's tool schemas, ``SOUL.md``, the prompt section code and the skill files
    the prompt's skill index is built from. A content hash and not a git revision: an unrelated
    commit must not cost every open chat a prompt-cache miss, and a release image has no ``.git``.
    """
    role = source.name
    package = source.parent / "core" / "src" / "research_core"
    schemas = [spec.schema(role) for spec in tools_for(role)]
    files = [source / "SOUL.md", *(package / module for module in PROMPT_SECTION_MODULES)]
    for skills in (package / "skills" / "common", package / "skills" / role):
        files.extend(sorted(skills.rglob("SKILL.md")))
    digest = hashlib.sha256(json.dumps(schemas, sort_keys=True).encode())
    for path in files:
        digest.update(str(path.relative_to(source.parent)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def refresh_frozen_sessions(
    profile: Path, source: Path, request_prompt_refresh: Callable[[], None], *, check: bool = False
) -> list[str]:
    """Refresh the sessions in ``profile`` if the agent surface changed; returns what happened."""
    state_db = profile / STATE_DB
    if not state_db.exists():
        return []  # no sessions yet; Hermes creates the file with the first one
    try:
        surface = agent_surface(source)
        with closing(sqlite3.connect(state_db)) as connection, connection:
            connection.execute("PRAGMA synchronous=FULL")
            row = connection.execute(
                "SELECT value FROM state_meta WHERE key = ?", (SURFACE_KEY,)
            ).fetchone()
            if row is not None and row[0] == surface:
                return []
            if check:
                return ["open sessions: would refresh tools and system prompts"]
            # First, because it cannot share the transaction below. If that fails, the next boot
            # asks again, and a second request is one more rebuild of the same prompt.
            request_prompt_refresh()
            connection.execute("UPDATE sessions SET tool_names = NULL")
            connection.execute(
                "INSERT INTO state_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SURFACE_KEY, surface),
            )
    except (sqlite3.Error, OSError, RuntimeError) as failure:
        return [f"open sessions: refresh failed, the next boot tries again: {failure}"]
    return ["open sessions: refreshed tools and system prompts"]
