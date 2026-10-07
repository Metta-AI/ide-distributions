"""Shared protocol-test helpers: a real canonical git repo with a FileQueue, and simulated
card workers that run in worktrees and end with ``card finish``. Used by the handoff-processing
(``tests/protocol/test_merge.py``), publication and comms contract tests."""

import json
from pathlib import Path

from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.queue.file_queue import FileQueue
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime.card_finish import finish
from autoresearch.runtime.merge import process_handoffs

HYPOTHESIS = {
    "statement": "Heroes attack before the creep is in kill range.",
    "prediction": "Waiting for kill range raises last hits.",
    "topics": [],
    "lever": "SK.last_hit",
    "state": "proposed",
}
HYPOTHESIS_BODY = "Line one.\n\nl2\n\nl3\n\nl4\n\nLine five.\n"
EPOCH = "e1"


class Worker:
    """One admitted card: its request, task, worktree and current run."""

    def __init__(self, board: "Board", spec: CardSpec, task_id: str):
        self.board = board
        self.spec = spec
        self.task_id = task_id
        self.worktree = board.canonical / ".worktrees" / spec.request_id
        self.run_id: str | None = None

    @property
    def request_id(self) -> str:
        return self.spec.request_id

    @property
    def store(self) -> RecordStore:
        return RecordStore(Layout(self.worktree))

    def ctx(self) -> RunContext:
        return self.board.ctx(worker=self.worktree, card_id=self.task_id, run_id=self.run_id)

    def start(self) -> str:
        """Claim the card (a new run) and create its worktree from canonical HEAD."""
        card = self.board.queue.claim_next(self.request_id)
        assert card is not None and card.task_id == self.task_id
        self.run_id = card.runs[-1].run_id
        if not self.worktree.exists():
            branch = f"card/{self.request_id}"
            gitops.git(self.board.canonical, "worktree", "add", "-b", branch, str(self.worktree))
        return self.run_id

    def write_journal(self) -> str:
        return self.store.create(
            "journal",
            {
                "iteration": 1,
                "mode": self.spec.mode,
                "card": self.task_id,
                "doctrine_version": 1,
            },
            "Did things.\n\n## Decisions\n\n- none\n",
        )

    def finish(self, outcome: str = "done", terminal: bool = True) -> dict:
        result = finish(self.ctx(), outcome)
        assert result.ok, result.problems
        if terminal:
            self.end_run(outcome)
        return result.record

    def end_run(self, outcome: str = "done") -> None:
        self.board.queue.finish(self.task_id, self.run_id, outcome)

    def handoff_path(self) -> Path:
        return self.board.ledger / "handoffs" / self.task_id / f"{self.run_id}.json"

    def handoff(self) -> dict:
        return self.board.committed(self.handoff_path())

    def receipt(self) -> dict:
        return self.board.committed(self.board.receipt_path(self.request_id))


class Board:
    """A canonical repo (with ``board.json``), a FileQueue, and helpers to admit cards."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.canonical = tmp_path / "canonical"
        self.canonical.mkdir()
        self.ledger = self.canonical / ".supervisor" / "ledger"
        self.queue = FileQueue(tmp_path / "queue")
        self.store = RecordStore(Layout(self.canonical))
        self._seed()

    def _seed(self) -> None:
        root = self.canonical
        gitops.git(root, "init", "-b", "main")
        gitops.git(root, "config", "user.email", "t@example.com")
        gitops.git(root, "config", "user.name", "T")
        (root / ".gitignore").write_text(".supervisor/runtime/\n.worktrees/\n")
        (root / "AGENTS.md").write_text("# Doctrine\n")
        (root / "strategy").mkdir()
        (root / "strategy" / "notes.md").write_text("# Notes\n\nCurrent plan: farm.\n")
        self.hypothesis = self.store.create("hypothesis", dict(HYPOTHESIS), HYPOTHESIS_BODY)
        write_json(self.ledger / "board.json", {"epoch": EPOCH})
        gitops.commit_all(root, "seed")

    def ctx(self, worker=None, card_id=None, run_id=None) -> RunContext:
        return RunContext(
            hermes_home=self.tmp_path / "home",
            canonical=self.canonical,
            worker=worker,
            card_id=card_id,
            run_id=run_id,
        )

    def receipt_path(self, request_id: str) -> Path:
        return self.ledger / "requests" / f"{request_id}.json"

    def committed(self, path: Path) -> dict | None:
        content = gitops.show(self.canonical, "HEAD", path.relative_to(self.canonical).as_posix())
        return None if content is None else json.loads(content)

    def admit(self, refs=(), mode="investigation", request_id=None) -> Worker:
        """What admission (Task 3.9) does: create and assign the card, commit the receipt and
        write the runtime card file. The assignee is the request id so each worker claims
        exactly its own card."""
        spec = CardSpec(
            request_id=request_id or new_id("card_request"),
            title="T",
            mode=mode,
            why="W",
            refs=list(refs),
            created_by="planner",
            priority="P2",
        )
        task_id = self.queue.create_unassigned(
            spec,
            branch=f"card/{spec.request_id}",
            worktree=self.canonical / ".worktrees" / spec.request_id,
        )
        self.queue.assign(task_id, spec.request_id)
        receipt_file = self.receipt_path(spec.request_id)
        write_json(
            receipt_file,
            {
                "request_id": spec.request_id,
                "state": "admitted",
                "mode": mode,
                "tasks": [{"task_id": task_id, "board_epoch": EPOCH}],
                "history": [{"state": "admitted", "at": "2026-10-07T00:00:00+00:00"}],
            },
        )
        gitops.commit_paths(
            self.canonical, [receipt_file.relative_to(self.canonical).as_posix()], "admit"
        )
        write_json(
            self.canonical / ".supervisor" / "runtime" / "cards" / f"{task_id}.json",
            {
                "task_id": task_id,
                "request_id": spec.request_id,
                "mode": mode,
                "refs": list(refs),
                "finished": False,
            },
        )
        return Worker(self, spec, task_id)

    def admit_follow_up(self, request_id: str) -> Worker:
        """Admit a runtime-written repair/reconcile request committed in canonical."""
        path = self.canonical / "cards" / "requests" / f"{request_id}.json"
        request = self.committed(path)
        spec = request["spec"]
        return self.admit(refs=spec["refs"], mode=spec["mode"], request_id=request_id)

    def process(self, publish=None, queue=None):
        return process_handoffs(self.ctx(), queue or self.queue, self.store, publish=publish)

    def merge_commits(self) -> list[str]:
        return gitops.git(self.canonical, "rev-list", "--merges", "HEAD").split()

    def follow_up(self, request_id: str) -> dict:
        return self.committed(self.canonical / "cards" / "requests" / f"{request_id}.json")


def new_hypothesis(worker: Worker) -> str:
    return worker.store.create("hypothesis", dict(HYPOTHESIS), "New.\n")
