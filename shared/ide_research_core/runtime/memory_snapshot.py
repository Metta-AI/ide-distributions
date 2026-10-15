"""Snapshot of the profile's Hermes memory into canonical (Plan 03 Task 5.2).

Hermes keeps the agent's memory in ``$HERMES_HOME/memories/MEMORY.md``, outside canonical, so a
lost host would lose it. After each merge pass queue-keeper copies it to
``canonical/.supervisor/hermes-memory.md`` and commits it (pushed with ``main``). Restore on a
new host is a copy back; the README describes it.
"""

from pathlib import Path

from ide_research_core.io.docs import write_text
from ide_research_core.runctx import RunContext
from ide_research_core.runtime.ledger import canonical_lock, commit

MEMORY_RELATIVE = Path("memories") / "MEMORY.md"
SNAPSHOT_RELATIVE = ".supervisor/hermes-memory.md"


def snapshot_memory(ctx: RunContext) -> bool:
    """Copy MEMORY.md into canonical and commit it. Return True when a commit was made
    (False when the file is absent or unchanged). Takes the canonical lock."""
    source = ctx.hermes_home / MEMORY_RELATIVE
    if not source.is_file():
        return False
    target = ctx.canonical / SNAPSHOT_RELATIVE
    text = source.read_text(encoding="utf-8")
    with canonical_lock(ctx.canonical):
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return False
        write_text(target, text)
        return commit(ctx.canonical, [target], "memory: snapshot MEMORY.md") is not None
