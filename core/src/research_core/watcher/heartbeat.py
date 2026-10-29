"""Step 12a: the heartbeat and the "has work" signal (design Appendix H "Sleep and drain").

Every tick writes ``meta.heartbeat`` (the tick's time) and ``meta.has_work`` (``"1"`` or
``"0"``) in the workspace store; the bridge reads the first to show "background work stopped
since …" and hosting reads the second as a reason to stay awake. A ``watcher.heartbeat``
event is appended only every :data:`EVENT_INTERVAL`, so the events log stays readable.

"has work" is ``drain.has_work`` (a running Researcher run, a Task waiting on XP, credits, a
submission or a background process, an open autonomous-submission window with a candidate)
or a queued wake; until ``drain`` is built, queued wakes alone decide.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

from research_core.store._common import now_iso, parse_iso
from research_core.watcher import wakes
from research_core.watcher.context import TickContext

HEARTBEAT_KEY = "heartbeat"
HAS_WORK_KEY = "has_work"
HEARTBEAT_EVENT_KEY = "heartbeat_event_at"
EVENT_INTERVAL = timedelta(minutes=10)
DRAIN_MODULE = "research_core.watcher.drain"


def run(ctx: TickContext) -> dict[str, Any]:
    # The stamp goes first: it is the liveness signal, so a has-work failure cannot hide it.
    stamp = now_iso(lambda: ctx.now)
    ctx.set_meta(HEARTBEAT_KEY, stamp)
    has_work = compute_has_work(ctx)
    ctx.set_meta(HAS_WORK_KEY, "1" if has_work else "0")
    emitted = False
    last = ctx.meta(HEARTBEAT_EVENT_KEY)
    if last is None or ctx.now - parse_iso(last) >= EVENT_INTERVAL:
        with ctx.wstore.tx(ctx.actor) as tx:
            tx.event("watcher.heartbeat", "watcher", {"at": stamp, "has_work": has_work})
        ctx.set_meta(HEARTBEAT_EVENT_KEY, stamp)
        emitted = True
    return {"heartbeat": stamp, "has_work": has_work, "event": emitted}


def compute_has_work(ctx: TickContext) -> bool:
    queued = wakes.has_queued_wakes(ctx)
    try:
        drain = importlib.import_module(DRAIN_MODULE)
    except ModuleNotFoundError as error:
        if error.name != DRAIN_MODULE:
            raise
        return queued
    has_work = getattr(drain, "has_work", None)
    if has_work is None:
        return queued
    return queued or bool(has_work(ctx))
