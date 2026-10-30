"""Shared fixtures for the store tests.

Later store tasks (goals, tasks, claims, ...) use these names: ``coworld_store`` and
``workspace_store`` are opened stores on a fresh temporary workspace, ``harness`` and
``session_actor`` are actors, and ``clock`` is the fake clock both stores read time from, so a
test can age a lease or a claim with ``clock.advance(seconds)``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_core.store.actors import Actor
from research_core.store.db import Store


class FakeClock:
    """A callable clock: returns a fixed aware datetime that tests move forward."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 28, 21, 4, 5, 123456, tzinfo=UTC))


@pytest.fixture
def coworld_store(tmp_path, clock):
    store = Store.open(tmp_path / "cow_x" / ".state" / "store.db", kind="coworld", clock=clock)
    yield store
    store.close()


@pytest.fixture
def workspace_store(tmp_path, clock):
    store = Store.open(tmp_path / ".state" / "store.db", kind="workspace", clock=clock)
    yield store
    store.close()


@pytest.fixture
def harness() -> Actor:
    return Actor.harness()


@pytest.fixture
def session_actor() -> Actor:
    return Actor(kind="session", id="api_1761690245_0badcafe")
