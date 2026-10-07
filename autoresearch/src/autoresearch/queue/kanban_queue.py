"""Queue backed by the real Hermes Kanban CLI (the production queue).

Every command is ``hermes kanban --board <board> ...``. Kanban boards are host-wide
(``<root>/kanban/boards/<slug>/kanban.db``), so no ``-p <profile>`` is needed for card
operations; ``profile`` is the worker profile the keeper assigns cards to. Command shapes
and JSON fields are verified in docs/plans/findings/hermes-facts.md (Hermes v0.21.5).
"""

import json
import os
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from autoresearch.queue.card import Card, CardSpec, Priority, RunInfo, kanban_priority

_STATE_BY_STATUS = {
    "triage": "pending",
    "todo": "pending",
    "scheduled": "pending",
    "ready": "pending",
    "running": "claimed",
    "review": "claimed",
    "blocked": "blocked",
    "done": "completed",
    "archived": "completed",
}


class KanbanError(RuntimeError):
    """A Hermes Kanban command failed or Hermes is not installed."""

    def __init__(self, argv: list[str], stderr: str):
        super().__init__(f"{' '.join(argv)}: {stderr.strip()}")
        self.argv = argv
        self.stderr = stderr


class KanbanQueue:
    def __init__(
        self,
        board: str,
        profile: str,
        hermes_bin: str = "hermes",
        run=subprocess.run,
        hermes_home: Path | None = None,
    ):
        self.board = board
        self.profile = profile
        self.hermes_bin = hermes_bin
        self._run = run
        self._env = dict(os.environ)
        if hermes_home is not None:
            self._env["HERMES_HOME"] = str(hermes_home)

    # -- command helpers --

    def _hermes(self, *args: str) -> str:
        argv = [self.hermes_bin, *args]
        try:
            result = self._run(argv, capture_output=True, text=True, env=self._env)
        except FileNotFoundError:
            raise KanbanError(argv, f"hermes not found: {self.hermes_bin}") from None
        if result.returncode != 0:
            raise KanbanError(argv, result.stderr)
        return result.stdout

    def _kanban(self, *args: str) -> str:
        return self._hermes("kanban", "--board", self.board, *args)

    @staticmethod
    def _parse_spec(task: dict) -> CardSpec:
        """The CardSpec in the task body; ValueError for a task we did not create."""
        try:
            return CardSpec(**json.loads(task["body"]))
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError(f"task {task['id']} has no autoresearch card body") from error

    @staticmethod
    def _run_info(run: dict) -> RunInfo:
        # Drops are recorded as outcome "completed" by Hermes; our metadata says "dropped".
        outcome = (run.get("metadata") or {}).get("outcome") or run["outcome"]
        return RunInfo(str(run["id"]), run["ended_at"] is not None, outcome)

    # -- Queue protocol --

    def ensure_board(self) -> None:
        """Create the board; Hermes treats an existing board as success."""
        self._hermes("kanban", "boards", "create", self.board)

    def create_unassigned(self, spec: CardSpec, branch: str, worktree: Path) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as body_file:
            body_file.write(json.dumps(asdict(spec)))
            body_file.flush()
            output = self._kanban(
                "create", spec.title,
                "--body-file", body_file.name,
                "--branch", branch,
                "--workspace", f"worktree:{worktree}",
                "--priority", str(kanban_priority(spec.priority, spec.order)),
                "--idempotency-key", f"req:{spec.request_id}",
                "--json",
            )  # fmt: skip
        return json.loads(output)["id"]

    def assign(self, task_id: str, assignee: str | None) -> None:
        self._kanban("assign", task_id, assignee or "none")

    def get(self, task_id: str) -> Card:
        shown = json.loads(self._kanban("show", task_id, "--json"))
        task = shown["task"]
        try:
            spec = self._parse_spec(task)
        except ValueError as error:
            raise KanbanError(["kanban", "show", task_id], str(error)) from error
        return Card(
            task_id=task["id"],
            spec=spec,
            state=_STATE_BY_STATUS[task["status"]],
            assignee=task["assignee"],
            runs=[self._run_info(run) for run in shown["runs"]],
        )

    def list(self, state: str | None = None) -> list[Card]:
        """Cards on the board (archived hidden), with runs; foreign tasks are skipped.

        Costs one ``show`` per card because the list JSON carries no runs.
        """
        cards = []
        for task in json.loads(self._kanban("list", "--json")):
            if state is not None and _STATE_BY_STATUS[task["status"]] != state:
                continue
            try:
                self._parse_spec(task)
            except ValueError:
                continue
            cards.append(self.get(task["id"]))
        return cards

    def reprioritize(self, task_id: str, priority: Priority, order: int = 0) -> None:
        number = kanban_priority(priority, order)  # validates the order range
        card = self._require_pending(task_id, "reprioritize")
        spec = asdict(card.spec) | {"priority": priority, "order": order}
        self._kanban("edit", task_id, "--priority", str(number), "--body", json.dumps(spec))

    def drop(self, task_id: str, reason: str) -> None:
        self._require_pending(task_id, "drop")
        self._kanban(
            "complete", task_id,
            "--summary", f"dropped: {reason}",
            "--metadata", '{"outcome":"dropped"}',
        )  # fmt: skip

    def requeue(self, task_id: str, reason: str) -> None:
        state = self.get(task_id).state
        if state == "blocked":
            self._kanban("unblock", task_id, "--reason", reason)
        elif state == "claimed":
            self._kanban("reclaim", task_id)
        else:
            raise ValueError(f"cannot requeue a {state} card")

    def _require_pending(self, task_id: str, action: str) -> Card:
        card = self.get(task_id)
        if card.state != "pending":
            raise ValueError(f"cannot {action} a {card.state} card; only pending cards")
        return card
