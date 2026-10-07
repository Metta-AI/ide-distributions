"""The Queue protocol implemented by the Kanban backend and by FileQueue."""

from pathlib import Path
from typing import Protocol

from autoresearch.queue.card import Card, CardSpec, Priority


class Queue(Protocol):
    def create_unassigned(self, spec: CardSpec, branch: str, worktree: Path) -> str:
        """Create a card nobody can claim yet. Idempotent on ``req:<request_id>``."""

    def assign(self, task_id: str, assignee: str | None) -> None:
        """Assign the card; ``None`` unassigns it."""

    def get(self, task_id: str) -> Card: ...

    def list(self, state: str | None = None) -> list[Card]: ...

    def reprioritize(self, task_id: str, priority: Priority, order: int = 0) -> None:
        """Pending cards only."""

    def drop(self, task_id: str, reason: str) -> None:
        """Pending cards only: complete the card with outcome ``dropped``."""

    def requeue(self, task_id: str, reason: str) -> None:
        """Return a blocked or claimed card to pending."""
