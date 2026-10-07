"""Fixtures for the comms contract suite: the protocol tests' Board (real git canonical,
FileQueue, simulated workers) and fake Preston consumers that use only the CLI."""

import os
import sys
from pathlib import Path

import pytest

# Reuse the Board/Worker helpers of the handoff-processing protocol tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "protocol"))

from fake_preston import FakePreston
from test_merge import Board


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
