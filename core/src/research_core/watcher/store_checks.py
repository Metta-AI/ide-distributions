"""Steps 1 and 2 of the tick: the pause check and store protection (design B.10, Appendix H).

``pause_state`` reads the workspace ``paused`` setting. ``run`` makes the store checks the
design puts in every tick, on the workspace store and the active Coworld's store:

* the schema of each open store is compared with the release's migrations; on a mismatch the
  store is repaired in place keeping its rows (``migrate.rebuild_keeping_rows``, which first
  saves a copy of the file next to it) and reopened in the context, with the event
  ``store.schema_rebuilt`` in the repaired store (a management wake, through the ownership
  map) and a notice naming the copy;
* a store a newer release wrote (``user_version`` above this release's migrations) is never
  repaired: it is left untouched, closed and taken out of the context, the workspace is paused
  so nothing writes through a shape this release does not know, and the event
  ``store.schema_newer`` (a management wake) and a notice are written to the workspace store,
  once per store and version;
* ``change_log`` rows no library transaction stamped are writes made outside the tools; each
  batch is reported once (event ``store.unmarked_write`` carrying the rows, a notice) and then
  acknowledged, so the next tick does not report it again.

``Store.open`` refuses a mismatched or newer file, so a schema change made between ticks
surfaces when the next tick builds its context, before any step runs. ``repair_unopenable`` is
the pre-open path ``tick.main`` takes then: it repairs what does not open and records the same
event and notice; a newer Coworld store is reported and paused the same way, and
``context.build`` then builds the tick without it. A newer *workspace* store cannot be reported
anywhere (the report itself would be a write through it), so it stops the tick with the error.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research_core import layout
from research_core.store import coworlds, migrate, notices, settings
from research_core.store.db import Store
from research_core.store.errors import SchemaMismatch, SchemaNewer
from research_core.watcher.context import WATCHER_ACTOR, TickContext

WORKSPACE_LABEL = "workspace"
# Unmarked rows carried in one event payload; the count says how many there were in all.
UNMARKED_PAYLOAD_ROWS = 200
DIFF_LIMIT = 50
# Workspace ``meta`` key per store label: the newer version already reported, so one report.
NEWER_REPORTED_KEY = "schema_newer:{label}"


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
        if status.newer:
            _set_aside_open_store(ctx, store, status.user_version, status.expected_version)
            _report_newer(ctx.wstore, label, status.user_version, status.expected_version)
            report[label] = {"schema_newer": status.user_version}
            continue
        if not status.ok:
            store = _rebuild_open_store(ctx, label, store, status.diff)
            entry["schema_rebuilt"] = len(status.diff)
        _forget_newer(ctx.wstore, label)
        changes = store.unmarked_changes()
        if changes:
            _report_unmarked(ctx, label, store, changes)
            entry["unmarked_writes"] = len(changes)
        report[label] = entry
    if ctx.coworld is not None and ctx.store is None:
        # context.build left the Coworld store out because a newer release wrote it.
        newer = newer_version(ctx.coworld.store_path, "coworld")
        if newer is not None:
            label = ctx.coworld.coworld_id
            _report_newer(ctx.wstore, label, newer, migrate.latest_version("coworld"))
            report[label] = {"schema_newer": newer}
    return report


def repair_unopenable(env: Mapping[str, str]) -> list[dict[str, Any]]:
    """Repair the stores whose schema no longer opens, before the tick builds its context.

    Returns one entry per repaired store. The workspace store is opened (and repaired if
    needed) first, because the active Coworld's store path is read from it. A Coworld store a
    newer release wrote is reported and the workspace paused instead; a newer workspace store
    raises ``SchemaNewer`` for ``tick.main`` to print.
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
                _open_or_rebuild_coworld(wstore, coworld, repaired)
        for entry in repaired:
            _notify_rebuild(wstore, entry)
    finally:
        wstore.close()
    return repaired


def newer_version(path: Path, kind: str) -> int | None:
    """The file's ``user_version`` when a newer release wrote it, read without opening a Store."""
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        version = migrate.user_version(conn)
    finally:
        conn.close()
    return version if version > migrate.latest_version(kind) else None


# --- helpers ---------------------------------------------------------------------------------


def _rebuild_open_store(ctx: TickContext, label: str, store: Store, diff: list[str]) -> Store:
    """Close ``store``, repair its file in place keeping rows, reopen it into ``ctx``, record it."""
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


def _open_or_rebuild_coworld(
    wstore: Store, coworld: layout.Coworld, repaired: list[dict[str, Any]]
) -> None:
    label = coworld.coworld_id
    try:
        store = _open_or_rebuild(coworld.store_path, "coworld", label, repaired)
    except SchemaNewer as newer:
        _report_newer(wstore, label, newer.user_version, newer.expected_version)
        return
    store.close()
    _forget_newer(wstore, label)


def _record_rebuild(
    store: Store, label: str, diff: list[str], rebuilt: migrate.RebuildReport
) -> dict[str, Any]:
    """The ``store.schema_rebuilt`` event in the repaired store; returns what the notice needs."""
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
        "backup": rebuilt.backup,
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
    text += f" A copy of the store as it was is at {entry['backup']}."
    notices.add(wstore, WATCHER_ACTOR, text=text, urgency="high")


def _set_aside_open_store(
    ctx: TickContext, store: Store, user_version: int, expected_version: int
) -> None:
    """Close a store that turned newer while open and take it out of the context.

    The workspace store itself is the only place a report could go, so a newer workspace store
    stops the step instead: the next tick's open refuses it and ``tick.main`` prints why.
    """
    if store.kind == "workspace":
        raise SchemaNewer(user_version, expected_version)
    store.close()
    ctx.store = None


def _report_newer(wstore: Store, label: str, user_version: int, expected_version: int) -> None:
    """Once per store and version: the event, a notice and the pause (citing the notice)."""
    key = NEWER_REPORTED_KEY.format(label=label)
    if wstore.get_meta(key) == str(user_version):
        return
    with wstore.tx(WATCHER_ACTOR) as tx:
        tx.event(
            "store.schema_newer",
            label,
            {
                "store": label,
                "user_version": user_version,
                "expected_version": expected_version,
            },
        )
    notice = notices.add(
        wstore,
        WATCHER_ACTOR,
        text=(
            f"The {_describe(label)} store was written by a newer release (schema version"
            f" {user_version}; this release knows {expected_version}). The watcher left it"
            " untouched and paused the workspace so nothing writes through a shape it does not"
            " know. Run the matching or a newer runtime, then resume the workspace."
        ),
        urgency="high",
    )
    if not settings.get(wstore, "paused"):
        settings.update(wstore, WATCHER_ACTOR, "paused", True, cites=notice["id"])
    wstore.set_meta(key, str(user_version))


def _forget_newer(wstore: Store, label: str) -> None:
    """The store opens again: a later newer version is reported afresh."""
    wstore.delete_meta(NEWER_REPORTED_KEY.format(label=label))


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
