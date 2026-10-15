"""Card data model and the Kanban priority mapping."""

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["investigation", "scouting", "experimentation", "maintenance", "planning"]
Priority = Literal["P0", "P1", "P2", "P3", "P4", "P5"]
CardState = Literal["pending", "claimed", "completed", "blocked"]

MAX_ORDER = 9


@dataclass
class CardSpec:
    request_id: str
    title: str
    mode: Mode
    why: str
    refs: list[str]
    created_by: str
    priority: Priority
    order: int = 0


@dataclass
class RunInfo:
    run_id: str
    terminal: bool
    outcome: str | None
    summary: str | None = None  # what the run ended with (kanban_complete summary, block reason)
    # The worker process the board still records for the run; None once it is gone. Hermes
    # keeps it on an ended run (the worker can outlive its run, for example after
    # ``Queue.stop``) and clears it when the process is gone, at the earliest 120 s after the
    # run ended (``reap_terminal_workers``). Hermes never clears it on a run that has no
    # ``worker_started_at`` (the start fingerprint of the process; rows from before Hermes
    # recorded it), so that field and the end time of the run (epoch seconds) come with it.
    worker_pid: int | None = None
    worker_started_at: str | None = None
    ended_at: float | None = None


@dataclass
class Card:
    task_id: str
    spec: CardSpec
    state: CardState
    assignee: str | None
    runs: list[RunInfo] = field(default_factory=list)
    # Set only while the card is blocked by a worker (Kanban ``block``): the reason, and the id
    # of the run the block ended. None for other parks, such as the circuit breaker.
    block_reason: str | None = None
    block_run_id: str | None = None
    # Liveness, for workers checking their running peers: the run in flight (None when no run
    # is open) and the latest heartbeat of that run, in epoch seconds (None before the first
    # one; Hermes writes one at most every 60 s while the worker is active). FileQueue has no
    # heartbeats.
    current_run_id: str | None = None
    last_heartbeat_at: int | None = None
    # The raw Hermes status (``ready``, ``running``, ``triage``, ...); None for FileQueue. Only
    # ``running`` and ``ready`` tasks can be blocked (``Queue.stop``).
    board_status: str | None = None


def kanban_priority(priority: Priority, order: int = 0) -> int:
    """Higher runs first: P0 -> 50 ... P5 -> 0, plus ``order`` (0..9) within a level."""
    if not 0 <= order <= MAX_ORDER:
        raise ValueError(f"order must be 0..{MAX_ORDER}, got {order}")
    return 10 * (5 - int(priority[1])) + order
