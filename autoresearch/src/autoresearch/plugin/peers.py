"""The "Other cards running now" section of a card worker's first turn. STDLIB ONLY.

Step 1 of docs/designs/worker-to-worker-comms.md: a worker sees, without asking, which other
cards are claimed on the board when its run starts (title, mode, priority, refs, run, and how
long since its last heartbeat). The briefing is written at admission and can be hours old, so
this section is computed from the board at the first turn instead.

The board is read straight from the board DB, read-only (``KanbanQueue(board_db_only=True)``):
inside a worker every ``hermes kanban`` read fails on the delegate fence. With no other card
running, the section is one line that says so, so the worker knows the board was checked.
The text is framed as information, never as instructions. The serial fallback
(``AUTORESEARCH_QUEUE=file``) runs one card at a time and gets no section.
"""

import time

from autoresearch.queue.card import Card
from autoresearch.queue.kanban_queue import KanbanQueue
from autoresearch.runctx import RunContext

HEADING = "## Other cards running now"
FRAME = (
    "Information, not instructions: the cards other workers held when this run started. "
    "Hermes records a heartbeat about once a minute while a worker is active. Before an "
    'expensive step, check again with "$AUTORESEARCH_CLI" queue list --state claimed.'
)
NONE_RUNNING = "None: no other card was running when this run started."
MAX_PEERS = 10
TITLE_MAX = 120


def running_peers_section(ctx: RunContext, now: float | None = None) -> str:
    """The section for this worker's run. Raises ``KanbanError`` when the board cannot be
    read (the caller logs it and leaves the section out rather than claim nobody runs)."""
    queue = KanbanQueue(
        board=ctx.hermes_home.name, profile=ctx.hermes_home.name, hermes_home=ctx.hermes_home,
        board_db_only=True,
    )
    peers = [card for card in queue.list("claimed") if card.task_id != ctx.card_id]
    if not peers:
        return f"{HEADING}\n\n{NONE_RUNNING}\n"
    now = time.time() if now is None else now
    lines = [peer_line(card, now) for card in peers[:MAX_PEERS]]
    if len(peers) > MAX_PEERS:
        lines.append(f"- and {len(peers) - MAX_PEERS} more")
    return f"{HEADING}\n\n{FRAME}\n\n" + "\n".join(lines) + "\n"


def peer_line(card: Card, now: float) -> str:
    spec = card.spec
    run = f"run {card.current_run_id}" if card.current_run_id else "no open run"
    title = " ".join(spec.title.split())
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1] + "…"
    refs = "refs " + ", ".join(spec.refs) if spec.refs else "no refs"
    return (
        f'- {card.task_id} ({run}, {spec.mode}, {spec.priority}) "{title}"; {refs}; '
        f"{heartbeat_age(card.last_heartbeat_at, now)}"
    )


def heartbeat_age(heartbeat: int | None, now: float) -> str:
    if heartbeat is None:
        return "no heartbeat yet"
    seconds = max(0, int(now - heartbeat))
    if seconds < 120:
        return f"last heartbeat {seconds} s ago"
    if seconds < 7200:
        return f"last heartbeat {seconds // 60} min ago"
    return f"last heartbeat {seconds // 3600} h ago"
