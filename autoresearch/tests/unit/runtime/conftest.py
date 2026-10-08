"""Fixtures: a real git canonical workspace, its RunContext, a FileQueue, and the fakegame."""

import pytest

from autoresearch.games.loader import load_game
from autoresearch.queue.file_queue import FileQueue
from autoresearch.runctx import RunContext
from runtime.workspace import AUTORESEARCH_DIR, Canonical


@pytest.fixture
def canonical(tmp_path) -> Canonical:
    root = tmp_path / "canonical"
    root.mkdir()
    handle = Canonical(root)
    handle.seed()
    return handle


@pytest.fixture
def ctx(tmp_path, canonical) -> RunContext:
    return RunContext(
        hermes_home=tmp_path / "home",
        canonical=canonical.root,
        worker=None,
        card_id=None,
        run_id=None,
    )


@pytest.fixture
def queue(tmp_path) -> FileQueue:
    return FileQueue(tmp_path / "queue")


@pytest.fixture
def game():
    return load_game(AUTORESEARCH_DIR, "fakegame")
