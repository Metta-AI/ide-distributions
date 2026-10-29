"""Store errors. Every refusal carries all of its problems at once (design B.2).

Messages never name the store path: tools show them to agents and the path stays out of every
prompt, skill and tool output (B.10). Say "the store", never where it is.
"""

from __future__ import annotations

from collections.abc import Sequence


class StoreError(Exception):
    """A refused request. ``problems`` lists everything wrong with it."""

    def __init__(self, problems: Sequence[str] | str) -> None:
        if isinstance(problems, str):
            problems = [problems]
        self.problems: list[str] = list(problems)
        super().__init__("; ".join(self.problems))


class SchemaMismatch(StoreError):
    """The store's shape differs from the release's migrations; nothing works through it."""


class NotFound(StoreError):
    """A referenced record does not exist."""


class LeaseError(StoreError):
    """A Task-scoped write from someone other than the holder at the current epoch."""
