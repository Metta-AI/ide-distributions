"""Read-only wake accounting and prompt rendering. Coordinator owns all wake transitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_core.store import events, settings
from research_core.store._common import parse_display_id
from research_core.store.events import Event
from research_core.watcher.context import TickContext
from research_core.watcher.handlers import MODEL_KINDS
from research_core.watcher.store_checks import labelled_stores

TEMPLATES_DIR = Path(__file__).parent / "wakes"
PLANNED_STOPS_PREFIX = "wake_planned_stops:"
PAYLOAD_CHARS = 4000


def has_queued_wakes(ctx: TickContext) -> bool:
    """Whether any model-wake event is still unacknowledged and not given up on."""
    max_attempts = int(settings.get(ctx.wstore, "recovery.max_attempts"))
    for label, store in labelled_stores(ctx):
        for event in events.unhandled(store, MODEL_KINDS):
            row = store.one("SELECT * FROM handled_events WHERE seq=?", (event.seq,))
            if (
                row is None
                or counted_attempts(ctx, label, event.seq, row["attempts"]) <= max_attempts
            ):
                return True
    return False


def counted_attempts(ctx: TickContext, label: str, seq: int, attempts: int) -> int:
    """The attempts that count toward ``recovery.max_attempts``: stops for sleep do not."""
    return attempts - int(ctx.meta(f"{PLANNED_STOPS_PREFIX}{label}:{seq}") or 0)


def render_wake(kind: str, event: Event, ctx: TickContext) -> str:
    """The wake prompt: ``wakes/<kind>.md`` with the event filled in."""
    return _fill(kind, event.kind, event.subject, _payload_text(event.payload), ctx)


def render_batch(kind: str, batch: list[Event], ctx: TickContext) -> str:
    """The wake prompt for a batch of events of one kind: the template rendered once, with every
    subject in ``{subject}`` and a list of ``{"subject", "payload"}`` entries, each cut at
    :data:`PAYLOAD_CHARS`, in ``{payload_json}``."""
    subjects = ", ".join(event.subject for event in batch)
    entries = [_payload_text({"subject": e.subject, "payload": e.payload}) for e in batch]
    payload = "[\n" + ",\n".join(entries) + "\n]"
    return _fill(kind, batch[0].kind, subjects, payload, ctx)


def _payload_text(payload: Any) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if len(text) > PAYLOAD_CHARS:
        text = text[:PAYLOAD_CHARS] + "\n… (payload cut here; the event has the rest)"
    return text


def _fill(kind: str, event_kind: str, subject: str, payload: str, ctx: TickContext) -> str:
    template = (TEMPLATES_DIR / f"{kind}.md").read_text(encoding="utf-8")
    coworld_id = ctx.coworld.coworld_id if ctx.coworld is not None else "none active"
    replacements = (
        ("{event_kind}", event_kind),
        ("{subject}", subject),
        ("{payload_json}", payload),
        ("{coworld_id}", coworld_id),
    )
    text = template
    for placeholder, value in replacements:
        text = text.replace(placeholder, value)
    return text


def _subject_ids(subject: str) -> dict[str, int]:
    """``task_id`` or ``goal_id`` for the Session row when the subject is such a record."""
    try:
        kind, number = parse_display_id(subject)
    except ValueError:
        return {}
    if kind == "task":
        return {"task_id": number}
    if kind == "goal":
        return {"goal_id": number}
    return {}


def _is_record_ref(subject: str) -> bool:
    try:
        parse_display_id(subject)
    except ValueError:
        return False
    return True
