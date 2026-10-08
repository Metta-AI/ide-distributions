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


@dataclass
class Card:
    task_id: str
    spec: CardSpec
    state: CardState
    assignee: str | None
    runs: list[RunInfo] = field(default_factory=list)


def kanban_priority(priority: Priority, order: int = 0) -> int:
    """Higher runs first: P0 -> 50 ... P5 -> 0, plus ``order`` (0..9) within a level."""
    if not 0 <= order <= MAX_ORDER:
        raise ValueError(f"order must be 0..{MAX_ORDER}, got {order}")
    return 10 * (5 - int(priority[1])) + order
