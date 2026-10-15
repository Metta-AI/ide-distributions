"""Planning cadence with a single planning owner (Overview C11; System Design §5.10, §10).

``planning_request`` runs in queue-keeper each run. It writes and commits a runtime planning
card request in canonical ``cards/requests/`` (admission picks it up like any other request)
when both hold:

- The trigger has no request yet. Cadence: ``now`` rounded down to a multiple of ``every``
  from midnight UTC gives the slot; its marker is ``planning:<YYYY-MM-DD-HH>`` of the slot
  start (2-hour default: 12:00-13:59 UTC is ``planning:2026-10-07-12``). Queue growth (the
  ``queue_growth`` signal, ``growth=True``): marker ``planning:growth:<YYYY-MM-DD>``, so at
  most one growth request a day.
- No planning card owns planning (C11): none is pending, claimed, finished but unpublished,
  or requested and not yet admitted (``routing.planning_owned``). A trigger that finds
  planning owned is dropped, not queued: the owning card plans anyway, and the next slot
  asks again.

The marker is in the request's ``why`` in brackets (``routing.request_card``), so a rerun after
a crash before the commit finds the written request and commits it instead of writing a
second one. ``why`` also gives the number of open planning-backlog entries; the refs are the
open and active Topics, so the briefing shows them.

Markers come from ``runtime.markers``. ``request_planning`` is the shared step. ``rebuild``
calls it with the marker ``planning:rebuild:<epoch>`` (one planning card after a board rebuild, C12).
"""

import datetime
from pathlib import Path

from ide_research_core.io.docs import read_json
from ide_research_core.kb.requests import has_marker
from ide_research_core.paths import Layout
from ide_research_core.queue.base import Queue
from ide_research_core.queue.card import CardSpec
from ide_research_core.records.store import RecordStore
from ide_research_core.runctx import RunContext
from ide_research_core.runtime.ledger import canonical_lock, commit, committed_json
from ide_research_core.runtime.markers import planning_growth_marker, planning_marker
from ide_research_core.runtime.routing import planning_backlog, planning_owned, request_card

RUNTIME = "runtime"
PLANNING_EVERY = datetime.timedelta(hours=2)
PLANNING_PRIORITY = "P1"
OPEN_TOPIC_STATES = ("open", "active")


def slot_marker(now: datetime.datetime, every: datetime.timedelta = PLANNING_EVERY) -> str:
    """The cadence marker of the slot that holds ``now``."""
    now = now.astimezone(datetime.UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return planning_marker(midnight + ((now - midnight) // every) * every)


def marked_request(canonical: Path, marker: str) -> Path | None:
    """The request file in ``cards/requests`` whose ``why`` carries ``marker``, if any
    (committed, or left uncommitted by a crash)."""
    for path in sorted(Layout(canonical).card_requests.glob("Q-*.json")):
        if has_marker(read_json(path).get("spec") or {}, marker):
            return path
    return None


def _open_topics(canonical: Path) -> list[str]:
    store = RecordStore(Layout(canonical))
    topics = [topic for state in OPEN_TOPIC_STATES for topic in store.find("topic", status=state)]
    return sorted(topics)


def _planning_spec(canonical: Path, reason: str) -> CardSpec:
    topics = _open_topics(canonical)
    backlog = len(planning_backlog(canonical))
    return CardSpec(
        request_id="",  # write_request assigns the id
        title="Plan the work queue",
        mode="planning",
        why=(
            f"{reason} Planning backlog: {backlog} open entries. "
            f"Open Topics: {len(topics)}. Groom the queue: reprioritize, drop, or request cards."
        ),
        refs=[f"ar://topic/{topic}" for topic in topics],
        created_by=RUNTIME,
        priority=PLANNING_PRIORITY,
    )


def commit_runtime_request(canonical: Path, marker: str, spec: CardSpec) -> CardSpec | None:
    """Write (or find a crash's uncommitted copy of) the runtime request carrying ``marker``
    and commit it. None if a request with ``marker`` is already committed. Call under the
    canonical lock."""
    existing = marked_request(canonical, marker)
    if existing is not None and committed_json(canonical, existing) is not None:
        return None
    path = request_card(canonical, marker, spec)
    commit(canonical, [path], f"runtime request {path.stem} [{marker}]")
    return CardSpec(**read_json(path)["spec"])


def request_planning(ctx: RunContext, queue: Queue, marker: str, reason: str) -> CardSpec | None:
    """Write and commit one planning request carrying ``marker``, unless a request with it is
    already committed or planning is owned. Returns the new request's spec, or None. Takes
    the canonical lock: the caller must not hold it."""
    canonical = ctx.canonical
    with canonical_lock(canonical):
        if marked_request(canonical, marker) is None and planning_owned(canonical, queue):
            return None
        return commit_runtime_request(canonical, marker, _planning_spec(canonical, reason))


def planning_request(
    ctx: RunContext,
    queue: Queue,
    now: datetime.datetime,
    every: datetime.timedelta = PLANNING_EVERY,
    growth: bool = False,
) -> CardSpec | None:
    """Request planning for the cadence slot of ``now`` (or, with ``growth``, for the
    queue-growth signal) under the single-owner rule; see the module docstring."""
    if growth:
        marker = planning_growth_marker(now)
        reason = "Queue growth: the Pending card count keeps rising despite planning."
    else:
        marker = slot_marker(now, every)
        reason = f"Scheduled planning (every {every})."
    return request_planning(ctx, queue, marker, reason)
