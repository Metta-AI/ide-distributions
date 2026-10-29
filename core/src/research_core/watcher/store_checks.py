"""Steps 1 and 2 of the tick: the pause check and store protection (design B.10, Appendix H).

``pause_state`` reads the workspace ``paused`` setting. ``run`` makes the store checks the
design puts in every tick, on the workspace store and the active Coworld's store:

* the schema of each open store is compared with the release's migrations; on a mismatch the
  store is closed, rebuilt keeping its rows and reopened in the context, with the event
  ``store.schema_rebuilt`` in the rebuilt store (a management wake, through the ownership
  map) and a notice;
* ``change_log`` rows no library transaction stamped are writes made outside the tools; each
  batch is reported once (event ``store.unmarked_write`` carrying the rows, a notice) and then
  acknowledged, so the next tick does not report it again.

``Store.open`` refuses a mismatched file, so a schema change made between ticks surfaces when
the next tick builds its context, before any step runs. ``repair_unopenable`` is the pre-open
path ``tick.main`` takes then: it rebuilds what does not open and records the same event and
notice, and the tick that follows finds nothing left to do.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research_core import layout
from research_core.store import coworlds, migrate, notices, settings
from research_core.store.db import Store
from research_core.store.errors import SchemaMismatch
from research_core.watcher.context import WATCHER_ACTOR, TickContext

WORKSPACE_LABEL = "workspace"
# Unmarked rows carried in one event payload; the count says how many there were in all.
UNMARKED_PAYLOAD_ROWS = 200
DIFF_LIMIT = 50


def labelled_stores(ctx: TickContext) -> list[tuple[str, Store]]:
    """``(label, store)`` for the stores a tick scans: ``workspace``, then the Coworld id."""
    stores = [(WORKSPACE_LABEL, ctx.wstore)]
    if ctx.store is not None and ctx.coworld is not None:
        stores.append((ctx.coworld.coworld_id, ctx.store))
    return stores


def pause_state(ctx: TickContext) -> bool:
    return bool(settings.get(ctx.wstore, "paused"))


def run(ctx: TickContext) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for label, store in labelled_stores(ctx):
        entry: dict[str, Any] = {}
        status = store.schema_status()
        if not status.ok:
            store = _rebuild_open_store(ctx, label, store, status.diff)
            entry["schema_rebuilt"] = len(status.diff)
        changes = store.unmarked_changes()
        if changes:
            _report_unmarked(ctx, label, store, changes)
            entry["unmarked_writes"] = len(changes)
        report[label] = entry
    return report


def repair_unopenable(env: Mapping[str, str]) -> list[dict[str, Any]]:
    """Rebuild the stores whose schema no longer opens, before the tick builds its context.

    Returns one entry per rebuilt store. The workspace store is opened (and rebuilt if needed)
    first, because the active Coworld's store path is read from it.
    """
    ws = layout.from_env(env)
    ws.ensure_dirs()
    repaired: list[dict[str, Any]] = []
    wstore = _open_or_rebuild(ws.store_path, "workspace", WORKSPACE_LABEL, repaired)
    try:
        active = coworlds.active(wstore)
        if active is not None:
            coworld = ws.coworld(active["coworld_id"])
            if coworld.store_path.exists():
                store = _open_or_rebuild(
                    coworld.store_path, "coworld", active["coworld_id"], repaired
                )
                store.close()
        for entry in repaired:
            _notify_rebuild(wstore, entry)
    finally:
        wstore.close()
    return repaired


# --- helpers ---------------------------------------------------------------------------------


def _rebuild_open_store(ctx: TickContext, label: str, store: Store, diff: list[str]) -> Store:
    """Close ``store``, rebuild its file keeping rows, reopen it into ``ctx`` and record it."""
    path, kind, clock = store.path, store.kind, store.clock
    store.close()
    rebuilt = migrate.rebuild_keeping_rows(path, kind)
    fresh = Store.open(path, kind=kind, clock=clock)
    if kind == "workspace":
        ctx.wstore = fresh
    else:
        ctx.store = fresh
    entry = _record_rebuild(fresh, label, diff, rebuilt)
    _notify_rebuild(ctx.wstore, entry)
    return fresh


def _open_or_rebuild(path: Path, kind: str, label: str, repaired: list[dict[str, Any]]) -> Store:
    try:
        return Store.open(path, kind=kind)
    except SchemaMismatch as mismatch:
        diff = list(mismatch.problems)
    rebuilt = migrate.rebuild_keeping_rows(path, kind)
    store = Store.open(path, kind=kind)
    repaired.append(_record_rebuild(store, label, diff, rebuilt))
    return store


def _record_rebuild(
    store: Store, label: str, diff: list[str], rebuilt: migrate.RebuildReport
) -> dict[str, Any]:
    """The ``store.schema_rebuilt`` event in the rebuilt store; returns what the notice needs."""
    with store.tx(WATCHER_ACTOR) as tx:
        tx.event(
            "store.schema_rebuilt",
            label,
            {
                "store": label,
                "differences": diff[:DIFF_LIMIT],
                "difference_count": len(diff),
                "rebuild": rebuilt.to_dict(),
            },
        )
    return {
        "store": label,
        "differences": len(diff),
        "dropped_tables": list(rebuilt.dropped_tables),
        "dropped_columns": dict(rebuilt.dropped_columns),
    }


def _notify_rebuild(wstore: Store, entry: dict[str, Any]) -> None:
    text = (
        f"The {_describe(entry['store'])} store's schema differed from this release"
        f" ({entry['differences']} difference(s)); the watcher rebuilt it from the migrations"
        " and kept its rows."
    )
    dropped = list(entry["dropped_tables"]) + [
        f"{table}.{column}"
        for table, columns in entry["dropped_columns"].items()
        for column in columns
    ]
    if dropped:
        text += f" Dropped (not part of the release's shape): {', '.join(dropped)}."
    notices.add(wstore, WATCHER_ACTOR, text=text, urgency="high")


def _report_unmarked(
    ctx: TickContext, label: str, store: Store, changes: list[dict[str, Any]]
) -> None:
    tables = sorted({change["tbl"] for change in changes})
    with store.tx(ctx.actor) as tx:
        tx.event(
            "store.unmarked_write",
            label,
            {
                "store": label,
                "count": len(changes),
                "tables": tables,
                "rows": changes[:UNMARKED_PAYLOAD_ROWS],
            },
        )
    notices.add(
        ctx.wstore,
        ctx.actor,
        text=(
            f"{len(changes)} write(s) to the {_describe(label)} store were made outside the"
            f" tools (tables: {', '.join(tables)}). Rows written that way skip validation and"
            " the events log; use the record tools instead."
        ),
        urgency="high",
    )
    store.acknowledge_unmarked([change["id"] for change in changes])


def _describe(label: str) -> str:
    return "workspace" if label == WORKSPACE_LABEL else f"Coworld {label}"
