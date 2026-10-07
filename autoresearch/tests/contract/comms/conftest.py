"""Fixtures for the comms contract suite: the shared protocol Board (real git canonical,
FileQueue, simulated workers; ``tests/support``) and fake Preston consumers that use only
the CLI."""

import os

import pytest
from fake_preston import FakePreston
from support.board import Board


@pytest.fixture
def board(tmp_path) -> Board:
    return Board(tmp_path)


@pytest.fixture
def base_env(tmp_path) -> dict[str, str]:
    """The test process's environment without any Hermes or autoresearch variables."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HERMES_", "AUTORESEARCH_"))
    }
    return env | {"HERMES_HOME": str(tmp_path / "home")}


@pytest.fixture
def preston(board, base_env) -> FakePreston:
    return FakePreston("preston", board.canonical, base_env)


@pytest.fixture
def ide(board, base_env) -> FakePreston:
    return FakePreston("ide", board.canonical, base_env)
