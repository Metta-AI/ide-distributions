"""A throwaway profile layout: HOME, profile home, canonical, a worker worktree, a sibling."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from autoresearch.runctx import RunContext


@dataclass
class Layout:
    home: Path
    hermes_home: Path
    canonical: Path
    worker: Path
    sibling: Path
    cli: Path

    def ctx(self, *, worker: bool = True) -> RunContext:
        return RunContext(
            hermes_home=self.hermes_home,
            canonical=self.canonical,
            worker=self.worker if worker else None,
            card_id="t_1" if worker else None,
            run_id="r_1" if worker else None,
        )


@pytest.fixture
def layout(tmp_path, monkeypatch) -> Layout:
    home = tmp_path / "user"
    hermes_home = tmp_path / "profile"
    canonical = hermes_home / "workspace"
    worker = canonical / ".worktrees" / "Q-1"
    sibling = canonical / ".worktrees" / "Q-2"
    cli = hermes_home / "bin" / "autoresearch"
    for directory in (home / ".softmax", worker / "notes", sibling, cli.parent):
        directory.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AUTORESEARCH_CLI", str(cli))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(canonical))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_RUN_ID", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.delenv("AUTORESEARCH_QUEUE", raising=False)
    return Layout(home, hermes_home, canonical, worker, sibling, cli)
