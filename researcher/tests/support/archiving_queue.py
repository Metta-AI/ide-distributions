"""A FileQueue wrapper with Hermes-style archiving, for tests of what the runtime does with an
archived card: ``list`` hides it, ``get`` reads it as ``completed`` with ``board_status``
``archived``, ``archived_task_ids`` names it (``KanbanQueue`` semantics), and ``drop`` of a
pending card archives it (how ``KanbanQueue.drop`` closes a card Hermes will not complete)."""

import dataclasses


class ArchivingQueue:
    def __init__(self, inner):
        self.inner = inner
        self.archived: set[str] = set()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def archive(self, task_id: str) -> None:
        """What a human's ``hermes kanban archive <card>`` does."""
        self.archived.add(task_id)

    def list(self, state=None):
        return [card for card in self.inner.list(state) if card.task_id not in self.archived]

    def get(self, task_id):
        card = self.inner.get(task_id)
        if task_id in self.archived:
            return dataclasses.replace(card, state="completed", board_status="archived")
        return card

    def archived_task_ids(self) -> set[str]:
        return set(self.archived)

    def drop(self, task_id: str, reason: str) -> None:
        if self.get(task_id).state != "pending":
            raise ValueError("cannot drop a card that is not pending")
        self.archive(task_id)
