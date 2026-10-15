"""Fixtures for protocol tests: a real canonical git repo with one admitted card, and the
card's worker worktree created with ``git worktree add`` on branch ``card/<Q-id>``."""

from pathlib import Path

import pytest
from ulid import ULID

from ide_research_core.ids import new_id
from ide_research_core.io import gitops
from ide_research_core.io.docs import write_doc, write_json
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.runctx import RunContext

HYPOTHESIS = {
    "statement": "Heroes attack before the creep is in kill range.",
    "prediction": "Waiting for kill range raises last hits.",
    "topics": [],
    "lever": "SK.last_hit",
    "state": "proposed",
}

CLAIM = {
    "kind": "mechanism",
    "status": "belief",
    "confidence": 0.7,
    "evidence": [],
    "revise_if": "A replay shows damage other than 10.",
    "relations": [],
    "topics": [],
    "valid_from_version": "65",
    "last_checked_version": "65",
}


class CardWorkspace:
    """A canonical repo with one admitted card (receipt + runtime card file) and its worktree.

    ``in_refs`` is a hypothesis the card's refs name; ``not_in_refs`` is one they do not."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.canonical = tmp_path / "canonical"
        self.canonical.mkdir()
        self.request_id = new_id("card_request")
        self.task_id = f"K-{ULID()}"
        self.worktree = self.canonical / ".worktrees" / self.request_id
        self.ledger = self.canonical / ".supervisor" / "ledger"
        self.card_file = (
            self.canonical / ".supervisor" / "runtime" / "cards" / f"{self.task_id}.json"
        )
        self._seed()
        self.refs = [f"ar://hypothesis/{self.in_refs}", f"ar://claim/{self.claim_in_refs}"]
        self._admit()
        gitops.git(
            self.canonical, "worktree", "add", "-b", f"card/{self.request_id}", str(self.worktree)
        )
        self.base_sha = gitops.head(self.canonical)

    def _seed(self) -> None:
        gitops.git(self.canonical, "init", "-b", "main")
        gitops.git(self.canonical, "config", "user.email", "t@example.com")
        gitops.git(self.canonical, "config", "user.name", "T")
        (self.canonical / ".gitignore").write_text(".supervisor/runtime/\n.worktrees/\n")
        (self.canonical / "AGENTS.md").write_text("# Doctrine\n")
        kb = self.canonical / "kb"
        kb.mkdir()
        (kb / "index.md").write_text("# Index\n")
        outbox = self.canonical / "comms" / "outbox"
        outbox.mkdir(parents=True)
        (outbox / f"00000001-{new_id('message')}.json").write_text("{}\n")
        store = RecordStore(Layout(self.canonical))
        self.in_refs = store.create("hypothesis", dict(HYPOTHESIS))
        self.not_in_refs = store.create("hypothesis", dict(HYPOTHESIS))
        self.claim_in_refs = store.create("claim", dict(CLAIM), "Damage is 10.", slug="damage")
        self.claim_not_in_refs = store.create("claim", dict(CLAIM), "Range is 3.")
        gitops.commit_all(self.canonical, "seed")

    def _admit(self) -> None:
        receipt = {
            "request_id": self.request_id,
            "state": "admitted",
            "mode": "investigation",
            "tasks": [{"task_id": self.task_id, "board_epoch": "e0"}],
            "history": [{"state": "admitted", "at": "2026-10-07T00:00:00+00:00"}],
        }
        write_json(self.receipt_path, receipt)
        spec = {
            "request_id": self.request_id,
            "title": "T",
            "mode": "investigation",
            "why": "W",
            "refs": self.refs,
            "created_by": "planner",
            "priority": "P2",
        }
        write_json(
            self.canonical / "cards" / "requests" / f"{self.request_id}.json",
            {"id": self.request_id, "op": "create", "spec": spec},
        )
        gitops.commit_all(self.canonical, f"admit {self.request_id}")
        write_json(
            self.card_file,
            {
                "task_id": self.task_id,
                "request_id": self.request_id,
                "mode": "investigation",
                "refs": self.refs,
                "finished": False,
            },
        )

    @property
    def receipt_path(self) -> Path:
        return self.ledger / "requests" / f"{self.request_id}.json"

    def handoff_path(self, run_id: str) -> Path:
        return self.ledger / "handoffs" / self.task_id / f"{run_id}.json"

    def ctx(self, run_id: str = "run-1") -> RunContext:
        return RunContext(
            hermes_home=self.tmp_path / "home",
            canonical=self.canonical,
            worker=self.worktree,
            card_id=self.task_id,
            run_id=run_id,
        )

    def env(self, run_id: str = "run-1") -> dict[str, str]:
        return {
            "HERMES_HOME": str(self.tmp_path / "home"),
            "AUTORESEARCH_WORKSPACE": str(self.canonical),
            "HERMES_KANBAN_WORKSPACE": str(self.worktree),
            "HERMES_KANBAN_TASK": self.task_id,
            "HERMES_KANBAN_RUN_ID": run_id,
        }

    @property
    def worker_store(self) -> RecordStore:
        return RecordStore(Layout(self.worktree))

    def write_journal(
        self, card: str | None = None, body: str = "Did things.\n\n## Decisions\n\n- none\n"
    ) -> str:
        """Write a journal for this card (or ``card``) in the worktree; return its id."""
        return self.worker_store.create(
            "journal",
            {
                "iteration": 1,
                "mode": "investigation",
                "card": card or self.task_id,
                "doctrine_version": 1,
            },
            body,
        )

    def stage_message(self, **fields) -> Path:
        message_id = new_id("message")
        message = {
            "schema": "ar-comms/0",
            "id": message_id,
            "kind": "note",
            "title": "T",
            "body": "B",
            "refs": [],
            "from": "research-agent",
            "created": "2026-10-07T00:00:00+00:00",
            "iteration": 1,
            "priority": "P2",
            "expects_response": False,
            "supersedes": None,
            "expires": None,
        }
        message.update(fields)
        path = self.worktree / "comms" / "staged" / f"{message_id}.json"
        write_json(path, message)
        return path

    def write_inbound(self, inbound_id: str, kind: str) -> None:
        """Commit an inbound delivery record in canonical (Task 3.7 creates these for real)."""
        path = self.ledger / "inbound" / f"{inbound_id}.json"
        write_json(path, {"id": inbound_id, "kind": kind, "state": "routed"})
        gitops.commit_paths(
            self.canonical, [path.relative_to(self.canonical).as_posix()], "inbound"
        )

    def write_worker_doc(self, rel_path: str, meta: dict, body: str) -> None:
        write_doc(self.worktree / rel_path, meta, body)


@pytest.fixture
def card(tmp_path) -> CardWorkspace:
    return CardWorkspace(tmp_path)
