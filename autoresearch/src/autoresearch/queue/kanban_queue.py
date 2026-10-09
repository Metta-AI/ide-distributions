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
from collections.abc import Mapping
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
# A Hermes status this table does not know (a newer Hermes) reads as ``claimed``, with the raw
# status in ``Card.board_status``: the card stays visible (spend caps price it, the rebuild
# check sees it on the board), and nothing requeues, repairs or orphans a claimed card.
UNKNOWN_STATUS_STATE = "claimed"
# ``created_by`` of the stand-in spec of a foreign card (``KanbanQueue.foreign_cards``).
FOREIGN_CREATOR = "foreign"


def _state(status: str) -> str:
    return _STATE_BY_STATUS.get(status, UNKNOWN_STATUS_STATE)


# Columns the queue reads, matching the fields of the Hermes JSON output.
_SELECT_TASKS = (
    "SELECT id, title, body, assignee, status, priority FROM tasks WHERE status != 'archived'"
)
_SELECT_TASK = "SELECT id, title, body, assignee, status, priority FROM tasks WHERE id = ?"
_SELECT_RUNS = (
    "SELECT id, ended_at, outcome, metadata, summary, last_heartbeat_at FROM task_runs"
    " WHERE task_id = ? ORDER BY id"
)


class KanbanError(RuntimeError):
    """A Hermes Kanban command failed or Hermes is not installed."""

    def __init__(self, argv: list[str], stderr: str):
        super().__init__(f"{' '.join(argv)}: {stderr.strip()}")
        self.argv = argv
        self.stderr = stderr


class ForeignCardError(KanbanError):
    """A board task whose body is not an autoresearch card spec: the runtime did not create it
    (admission writes the spec into every card it creates), so it has no request behind it.
    Something added it to the board directly, such as a model's ``kanban_create``."""

    def __init__(self, argv: list[str], task_id: str, title: str | None, board: str):
        super().__init__(argv, f"task {task_id} has no autoresearch card body")
        self.task_id = task_id
        self.title = title
        self.board = board


# Substring of Hermes' fence error (kanban_db.py:147, raised from write_txn at
# kanban_db_connect.py:~1201 and from the CLI guard at kanban.py:153).
_DELEGATE_FENCE_MESSAGE = "cannot mutate Kanban"
# Names a board DB read in a KanbanError when no Hermes command ran (``board_db_only``).
_BOARD_DB_ARGV = ["board-db"]
# Provider of every per-mode model: the profile's model.provider (config.yaml). Pinned with
# the model so the worker resolves it on the intended backend (Hermes kanban_parser --provider).
MODEL_PROVIDER = "openrouter"
# Hermes statuses ``kanban block`` accepts (``kanban_db.block_task``).
STOPPABLE_STATUSES = ("running", "ready")


class KanbanQueue:
    def __init__(
        self,
        board: str,
        profile: str,
        hermes_bin: str = "hermes",
        run=subprocess.run,
        hermes_home: Path | None = None,
        board_db_only: bool = False,
        models: Mapping[str, str] | None = None,
        model_provider: str = MODEL_PROVIDER,
    ):
        """``board_db_only``: read the board DB directly (read-only) and never run Hermes, for
        callers inside a worker process (the plugin), where every read command would fail on
        the delegate fence after a costly subprocess start. Mutations still go to Hermes.

        ``models``: card mode -> model id. A card of a listed mode is created with Hermes'
        per-task model override (``kanban create --model <id> --provider <model_provider>``;
        the dispatcher spawns its worker with ``-m <id> --provider ...``,
        ``kanban_db_dispatch._worker_argv``). Other modes run on the profile's
        ``model.default``."""
        self.board = board
        self.models = dict(models or {})
        self.model_provider = model_provider
        self.board_db_only = board_db_only
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
        if self.board_db_only:
            return self._read_board_db(_BOARD_DB_ARGV, _SELECT_TASKS)
        try:
            return json.loads(self._kanban("list", "--json"))
        except KanbanError as error:
            if _DELEGATE_FENCE_MESSAGE not in error.stderr:
                raise
            return self._read_board_db(error.argv, _SELECT_TASKS)

    def _show_task(self, task_id: str) -> dict:
        """``kanban show --json`` as ``{"task": ..., "runs": [...]}`` (same fallback as list)."""
        if self.board_db_only:
            return self._show_from_board_db(_BOARD_DB_ARGV, task_id)
        try:
            return json.loads(self._kanban("show", task_id, "--json"))
        except KanbanError as error:
            if _DELEGATE_FENCE_MESSAGE not in error.stderr:
                raise
            return self._show_from_board_db(error.argv, task_id)

    def _show_from_board_db(self, argv: list[str], task_id: str) -> dict:
        tasks = self._read_board_db(argv, _SELECT_TASK, (task_id,))
        if not tasks:
            raise KanbanError(argv, f"no such task: {task_id}")
        runs = self._read_board_db(argv, _SELECT_RUNS, (task_id,))
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
        return RunInfo(str(run["id"]), run["ended_at"] is not None, outcome, run.get("summary"))

    @staticmethod
    def _liveness(shown: dict) -> tuple[str | None, int | None]:
        """``(current run id, its latest heartbeat)``. The current run is the open one. Its
        heartbeat is ``task_runs.last_heartbeat_at`` on the board DB path; Hermes' show JSON
        has no such field, so there it is the run's latest ``heartbeat`` event (Hermes
        ``heartbeat_worker`` writes the column and the event in one transaction)."""
        current = next((run for run in reversed(shown["runs"]) if run["ended_at"] is None), None)
        if current is None:
            return None, None
        heartbeat = current.get("last_heartbeat_at")
        if heartbeat is None:
            beats = [
                event["created_at"] for event in shown.get("events") or []
                if event.get("kind") == "heartbeat" and event.get("run_id") == current["id"]
            ]
            heartbeat = max(beats, default=None)
        return str(current["id"]), heartbeat

    @staticmethod
    def _blocking_run(status: str, runs: list[dict]) -> dict | None:
        """The run a worker's block ended, if the card is blocked and that run is the latest.
        Hermes ends it with the block reason as its summary. A card the circuit breaker parked
        later has no ``blocked`` run after it, so an old reason is never read as current."""
        if status == "blocked" and runs and runs[-1]["outcome"] == "blocked":
            return runs[-1]
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
        model = self.models.get(spec.mode)
        pin = ["--model", model, "--provider", self.model_provider] if model else []
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
                *pin,
                "--json",
            )  # fmt: skip
        return json.loads(output)["id"]

    def assign(self, task_id: str, assignee: str | None) -> None:
        self._kanban("assign", task_id, assignee or "none")

    def _card(self, shown: dict, spec: CardSpec) -> Card:
        task = shown["task"]
        blocking = self._blocking_run(task["status"], shown["runs"])
        current_run_id, last_heartbeat_at = self._liveness(shown)
        return Card(
            task_id=task["id"],
            spec=spec,
            state=_state(task["status"]),
            assignee=task["assignee"],
            runs=[self._run_info(run) for run in shown["runs"]],
            block_reason=blocking.get("summary") if blocking else None,
            block_run_id=str(blocking["id"]) if blocking else None,
            current_run_id=current_run_id,
            last_heartbeat_at=last_heartbeat_at,
            board_status=task["status"],
        )

    def get(self, task_id: str) -> Card:
        shown = self._show_task(task_id)
        task = shown["task"]
        try:
            spec = self._parse_spec(task)
        except ValueError as error:
            raise ForeignCardError(
                ["kanban", "show", task_id], task_id, task.get("title"), self.board
            ) from error
        return self._card(shown, spec)

    def foreign_cards(self) -> list[Card]:
        """The tasks ``list`` skips, those with no card body (archived hidden), with runs, so
        the runtime can report them and apply the spend caps to them. Their ``spec`` is a
        stand-in: only ``title`` is real (``request_id`` is empty, ``created_by`` is
        ``FOREIGN_CREATOR``, the other fields are placeholders); everything else comes from
        the board."""
        cards = []
        for task in self._list_tasks():
            try:
                self._parse_spec(task)
                continue
            except ValueError:
                pass
            try:
                shown = self._show_task(task["id"])
            except KanbanError as error:
                if "no such task" in error.stderr:
                    continue
                raise
            title = shown["task"].get("title") or ""
            spec = CardSpec("", title, "investigation", "", [], FOREIGN_CREATOR, "P5")
            cards.append(self._card(shown, spec))
        return cards

    def list(self, state: str | None = None) -> list[Card]:
        """Cards on the board (archived hidden), with runs; foreign tasks are skipped (see
        ``foreign_cards``).

        Costs one ``show`` per card because the list JSON carries no runs. A card deleted
        between the list and its ``show`` is skipped.
        """
        cards = []
        for task in self._list_tasks():
            if state is not None and _state(task["status"]) != state:
                continue
            try:
                self._parse_spec(task)
            except ValueError:
                continue
            try:
                cards.append(self.get(task["id"]))
            except KanbanError as error:
                if "no such task" not in error.stderr:
                    raise
        return cards

    def reprioritize(self, task_id: str, priority: Priority, order: int = 0) -> None:
        number = kanban_priority(priority, order)  # validates the order range
        card = self._require_pending(task_id, "reprioritize")
        spec = asdict(card.spec) | {"priority": priority, "order": order}
        self._kanban("edit", task_id, "--priority", str(number), "--body", json.dumps(spec))

    def drop(self, task_id: str, reason: str) -> None:
        """Close a pending card without doing it. A ``ready`` card is completed with outcome
        ``dropped`` in its run metadata; Hermes refuses ``complete`` from the other pending
        statuses (``todo``, ``triage``, ``scheduled``; ``kanban_db.complete_task``) and, for
        a child, while a parent is unfinished. Those are archived instead, after a comment
        with the reason: ``archive`` takes any status and ignores parent links, and the
        dispatcher never claims a card in those statuses, so archiving cannot end a
        worker's run. Unlinking the parent and then completing would make the card
        ``ready``, and the dispatcher could start it between the two commands. An archived
        card reads as ``completed`` with ``board_status`` ``archived`` (``queue_ops``
        recognises it as dropped on a rerun)."""
        card = self._require_pending(task_id, "drop")
        if card.board_status == "ready":
            self._kanban(
                "complete", task_id,
                "--summary", f"dropped: {reason}",
                "--metadata", '{"outcome":"dropped"}',
            )  # fmt: skip
            return
        self._kanban("comment", task_id, f"dropped: {reason}")
        self._kanban("archive", task_id)

    def requeue(self, task_id: str, reason: str) -> None:
        state = self.get(task_id).state
        if state == "blocked":
            self._kanban("unblock", task_id, "--reason", reason)
        elif state == "claimed":
            self._kanban("reclaim", task_id)
        else:
            raise ValueError(f"cannot requeue a {state} card")

    def stop(self, task_id: str, reason: str) -> str:
        """``kanban block --kind <kind>``: a ``running`` or ``ready`` task becomes ``blocked``
        and its run ends with the reason as summary (``kanban_db.block_task``; a never-claimed
        task gets a zero-length blocked run). Returns the Hermes status the task landed in.

        The kind is ``capability`` (a human-bucket kind no worker path uses), or
        ``needs_input`` when the task's latest block event already was ``capability``: Hermes counts
        same-kind block -> unblock -> block cycles and sends the second one to ``triage``
        (``_route_block``, ``BLOCK_RECURRENCE_LIMIT`` 2), which has no command back to
        ``ready``. The runtime never unblocks a spend-cap stop itself, so a repeat here is a
        human's deliberate unblock, not the automatic loop the breaker exists for.

        The worker of a running task is not signalled here: the dispatcher's
        ``reap_terminal_workers`` ends a worker that outlived its run, 120 s after the run
        ended, on its next tick. ValueError for another status."""
        shown = self._show_task(task_id)
        task = shown["task"]
        if task["status"] not in STOPPABLE_STATUSES:
            raise ValueError(f"cannot stop a {task['status']} task")
        kind = "needs_input" if self._last_block_kind(shown) == "capability" else "capability"
        self._kanban("block", task_id, reason, "--kind", kind)
        return self._show_task(task_id)["task"]["status"]

    @staticmethod
    def _last_block_kind(shown: dict) -> str | None:
        """The kind of the task's latest block. The show JSON has no ``block_kind`` field, so
        it is read from the latest ``blocked``/``block_loop_detected`` event's payload."""
        for event in reversed(shown.get("events") or []):
            if event.get("kind") in ("blocked", "block_loop_detected"):
                return (event.get("payload") or {}).get("kind")
        return None

    def _require_pending(self, task_id: str, action: str) -> Card:
        card = self.get(task_id)
        if card.state != "pending":
            raise ValueError(f"cannot {action} a {card.state} card; only pending cards")
        return card
