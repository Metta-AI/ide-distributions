"""The Queue protocol implemented by the Kanban backend and by FileQueue."""

from pathlib import Path
from typing import Protocol

from ide_research_core.queue.card import Card, CardSpec, Priority


class Queue(Protocol):
    def create_unassigned(
        self, spec: CardSpec, branch: str, worktree: Path, idempotency_key: str | None = None
    ) -> str:
        """Create a card nobody can claim yet. Idempotent on ``idempotency_key``, by default
        ``req:<request_id>`` (a C12 re-admission passes ``req:<request_id>:<epoch>``)."""

    def assign(self, task_id: str, assignee: str | None) -> None:
        """Assign the card; ``None`` unassigns it."""

    def get(self, task_id: str) -> Card:
        """The card. Unknown task: FileQueue raises KeyError, Kanban a ``KanbanError``. A Kanban
        task the runtime did not create (no card body) raises ``ForeignCardError``."""

    def foreign_cards(self) -> list[Card]:
        """Board tasks the runtime did not create (no card body), which ``list`` skips; their
        spec is a stand-in with only the title real. Always empty for FileQueue."""

    def list(self, state: str | None = None) -> list[Card]: ...

    def reprioritize(self, task_id: str, priority: Priority, order: int = 0) -> None:
        """Pending cards only."""

    def drop(self, task_id: str, reason: str) -> None:
        """Pending cards only: close the card without doing it (completed with outcome
        ``dropped``; Kanban archives a card Hermes will not complete, see
        ``KanbanQueue.drop``)."""

    def requeue(self, task_id: str, reason: str) -> None:
        """Return a blocked or claimed card to pending."""

    def stop(self, task_id: str, reason: str) -> str:
        """Block a pending or claimed card with ``reason`` (the runtime's spend caps); return
        the board status it landed in (``blocked``, or Hermes ``triage``). A claimed card's run
        ends with outcome ``blocked``; Hermes ends its worker process later, and until then the
        run keeps its ``RunInfo.worker_pid``."""
