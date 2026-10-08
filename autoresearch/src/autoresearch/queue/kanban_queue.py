"""Queue backed by the real Hermes Kanban CLI (the production queue).

Every command is ``hermes kanban --board <board> ...``. Kanban boards are host-wide
(``<root>/kanban/boards/<slug>/kanban.db``), so no ``-p <profile>`` is needed for card
operations; ``profile`` is the worker profile the keeper assigns cards to. Command shapes
and JSON fields are verified in docs/plans/findings/hermes-facts.md (Hermes v0.21.5).
"""

import json
import os
import sqlite3
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


# Columns the queue reads, matching the fields of the Hermes JSON output.
_SELECT_TASKS = (
    "SELECT id, title, body, assignee, status, priority FROM tasks WHERE status != 'archived'"
)
_SELECT_TASK = "SELECT id, title, body, assignee, status, priority FROM tasks WHERE id = ?"
_SELECT_RUNS = (
    "SELECT id, ended_at, outcome, metadata, summary FROM task_runs WHERE task_id = ? ORDER BY id"
)


class KanbanError(RuntimeError):
    """A Hermes Kanban command failed or Hermes is not installed."""

    def __init__(self, argv: list[str], stderr: str):
        super().__init__(f"{' '.join(argv)}: {stderr.strip()}")
        self.argv = argv
        self.stderr = stderr


# Substring of Hermes' fence error (kanban_db.py:147, raised from write_txn at
# kanban_db_connect.py:~1201 and from the CLI guard at kanban.py:153).
_DELEGATE_FENCE_MESSAGE = "cannot mutate Kanban"


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

    def _list_tasks(self) -> list[dict]:
        """``kanban list --json``; read straight from the board DB inside a delegate child.

        ``hermes kanban list`` runs ``recompute_ready`` (a write), which Hermes refuses in
        ``delegate_task`` children. The refusal is the same on every call, so fall back to a
        read-only DB read only for that message; mutating commands never take this path.
        """
        try:
            return json.loads(self._kanban("list", "--json"))
        except KanbanError as error:
            if _DELEGATE_FENCE_MESSAGE not in error.stderr:
                raise
            return self._read_board_db(error.argv, _SELECT_TASKS)

    def _show_task(self, task_id: str) -> dict:
        """``kanban show --json`` as ``{"task": ..., "runs": [...]}`` (same fallback as list)."""
        try:
            return json.loads(self._kanban("show", task_id, "--json"))
        except KanbanError as error:
            if _DELEGATE_FENCE_MESSAGE not in error.stderr:
                raise
            tasks = self._read_board_db(error.argv, _SELECT_TASK, (task_id,))
            if not tasks:
                raise KanbanError(error.argv, f"no such task: {task_id}") from None
            runs = self._read_board_db(error.argv, _SELECT_RUNS, (task_id,))
            for run in runs:
                run["metadata"] = self._tolerant_json_object(run["metadata"])
            return {"task": tasks[0], "runs": runs}

    @staticmethod
    def _tolerant_json_object(text: str | None) -> dict | None:
        """Run metadata is free-form text; anything that is not a JSON object becomes None."""
        try:
            value = json.loads(text) if text else None
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _board_db_path(self) -> Path:
        """The board DB path, resolved like Hermes ``kanban_db_path`` (kanban_db.py:495-510).

        ``HERMES_KANBAN_DB`` (injected into every dispatcher worker) wins; else the default
        board is ``<root>/kanban.db``, any other board ``<root>/kanban/boards/<slug>/kanban.db``
        (hermes-facts A2).
        """
        if pinned := self._env.get("HERMES_KANBAN_DB", "").strip():
            return Path(pinned).expanduser()
        root = self._hermes_root()
        slug = self.board.strip().lower()
        if slug == "default":
            return root / "kanban.db"
        return root / "kanban" / "boards" / slug / "kanban.db"

    def _hermes_root(self) -> Path:
        """``HERMES_KANBAN_HOME``, else Hermes ``get_default_hermes_root()`` (hermes_constants.py:217).

        Replicated, not imported: Hermes is a separate install. Non-Windows branch only.
        """
        if override := self._env.get("HERMES_KANBAN_HOME", "").strip():
            return Path(override).expanduser()
        native_home = Path.home() / ".hermes"
        env_home = self._env.get("HERMES_HOME", "").strip()
        if not env_home:
            return native_home
        home = Path(os.path.expanduser(os.path.expandvars(env_home)))
        try:
            home.resolve().relative_to(native_home.resolve())
        except ValueError:  # custom root: <root>/profiles/<name> -> <root>, else HERMES_HOME itself
            return home.parent.parent if home.parent.name == "profiles" else home
        return native_home

    def _read_board_db(self, argv: list[str], query: str, params: tuple = ()) -> list[dict]:
        db_path = self._board_db_path()
        if not db_path.exists():
            raise KanbanError(argv, f"board {self.board!r} does not exist")
        try:
            connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in connection.execute(query, params)]
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise KanbanError(argv, f"read-only board DB read failed: {error}") from error

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

    @staticmethod
    def _block_reason(status: str, runs: list[dict]) -> str | None:
        """Hermes ends the blocking run with the block reason as its summary."""
        if status != "blocked":
            return None
        for run in reversed(runs):
            if run["outcome"] == "blocked":
                return run.get("summary")
        return None

    # -- Queue protocol --

    def ensure_board(self) -> None:
        """Create the board; Hermes treats an existing board as success."""
        self._hermes("kanban", "boards", "create", self.board)

    def board_exists(self) -> bool:
        """False when Hermes reports the board missing (A2: its DB or list entry is gone)."""
        try:
            self._list_tasks()
        except KanbanError as error:
            if "does not exist" in error.stderr:
                return False
            raise
        return True

    def create_unassigned(
        self, spec: CardSpec, branch: str, worktree: Path, idempotency_key: str | None = None
    ) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as body_file:
            body_file.write(json.dumps(asdict(spec)))
            body_file.flush()
            output = self._kanban(
                "create", spec.title,
                "--body-file", body_file.name,
                "--branch", branch,
                "--workspace", f"worktree:{worktree}",
                "--priority", str(kanban_priority(spec.priority, spec.order)),
                "--idempotency-key", idempotency_key or f"req:{spec.request_id}",
                "--json",
            )  # fmt: skip
        return json.loads(output)["id"]

    def assign(self, task_id: str, assignee: str | None) -> None:
        self._kanban("assign", task_id, assignee or "none")

    def get(self, task_id: str) -> Card:
        shown = self._show_task(task_id)
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
            block_reason=self._block_reason(task["status"], shown["runs"]),
        )

    def list(self, state: str | None = None) -> list[Card]:
        """Cards on the board (archived hidden), with runs; foreign tasks are skipped.

        Costs one ``show`` per card because the list JSON carries no runs.
        """
        cards = []
        for task in self._list_tasks():
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
