"""File-backed Queue: the test backend and the serial-fallback backend.

One JSON file per card under ``<directory>/cards/`` plus ``index.json`` (idempotency keys
and a creation counter). Every mutation runs under ``<directory>/queue.lock``.
"""

from dataclasses import asdict
from pathlib import Path

from ulid import ULID

from autoresearch.io.docs import read_json, write_json
from autoresearch.io.locks import file_lock
from autoresearch.queue.card import Card, CardSpec, Priority, RunInfo, kanban_priority


class FileQueue:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._cards_dir = self.directory / "cards"
        self._index_path = self.directory / "index.json"
        self._lock_path = self.directory / "queue.lock"

    # -- storage helpers (call under the lock when mutating) --

    def _card_path(self, task_id: str) -> Path:
        return self._cards_dir / f"{task_id}.json"

    def _load_record(self, task_id: str) -> dict:
        path = self._card_path(task_id)
        if not path.exists():
            raise KeyError(f"unknown card: {task_id}")
        return read_json(path)

    def _save_record(self, record: dict) -> None:
        write_json(self._card_path(record["task_id"]), record)

    def _load_index(self) -> dict:
        if self._index_path.exists():
            return read_json(self._index_path)
        return {"keys": {}, "next_seq": 0}

    @staticmethod
    def _to_card(record: dict) -> Card:
        return Card(
            task_id=record["task_id"],
            spec=CardSpec(**record["spec"]),
            state=record["state"],
            assignee=record["assignee"],
            runs=[RunInfo(**run) for run in record["runs"]],
        )

    # -- Queue protocol --

    def create_unassigned(self, spec: CardSpec, branch: str, worktree: Path) -> str:
        key = f"req:{spec.request_id}"
        with file_lock(self._lock_path):
            index = self._load_index()
            if key in index["keys"]:
                return index["keys"][key]
            task_id = f"K-{ULID()}"
            record = {
                "task_id": task_id,
                "seq": index["next_seq"],
                "spec": asdict(spec),
                "state": "pending",
                "assignee": None,
                "runs": [],
                "branch": branch,
                "worktree": str(worktree),
            }
            self._save_record(record)
            index["keys"][key] = task_id
            index["next_seq"] += 1
            write_json(self._index_path, index)
            return task_id

    def assign(self, task_id: str, assignee: str | None) -> None:
        with file_lock(self._lock_path):
            record = self._load_record(task_id)
            record["assignee"] = assignee
            self._save_record(record)

    def get(self, task_id: str) -> Card:
        return self._to_card(self._load_record(task_id))

    def list(self, state: str | None = None) -> list[Card]:
        records = [read_json(path) for path in self._cards_dir.glob("K-*.json")]
        records.sort(key=lambda record: record["seq"])
        return [self._to_card(r) for r in records if state is None or r["state"] == state]

    def reprioritize(self, task_id: str, priority: Priority, order: int = 0) -> None:
        kanban_priority(priority, order)  # validates the order range
        with file_lock(self._lock_path):
            record = self._load_record(task_id)
            self._require_pending(record, "reprioritize")
            record["spec"]["priority"] = priority
            record["spec"]["order"] = order
            self._save_record(record)

    def drop(self, task_id: str, reason: str) -> None:
        with file_lock(self._lock_path):
            record = self._load_record(task_id)
            self._require_pending(record, "drop")
            record["state"] = "completed"
            record["drop_reason"] = reason
            record["runs"].append(asdict(RunInfo(f"r-{ULID()}", True, "dropped")))
            self._save_record(record)

    def requeue(self, task_id: str, reason: str) -> None:
        with file_lock(self._lock_path):
            record = self._load_record(task_id)
            if record["state"] not in ("blocked", "claimed"):
                raise ValueError(f"cannot requeue a {record['state']} card")
            for run in record["runs"]:
                if not run["terminal"]:
                    run["terminal"] = True
                    run["outcome"] = f"requeued: {reason}"
            record["state"] = "pending"
            self._save_record(record)

    # -- FileQueue extras --

    def claim_next(self, worker: str) -> Card | None:
        """Claim the highest-priority, then oldest, pending card assigned to ``worker``."""
        with file_lock(self._lock_path):
            candidates = [read_json(path) for path in self._cards_dir.glob("K-*.json")]
            candidates = [
                r for r in candidates if r["state"] == "pending" and r["assignee"] == worker
            ]
            if not candidates:
                return None
            best = max(
                candidates,
                key=lambda r: (
                    kanban_priority(r["spec"]["priority"], r["spec"]["order"]),
                    -r["seq"],
                ),
            )
            best["state"] = "claimed"
            best["runs"].append(asdict(RunInfo(f"r-{ULID()}", False, None)))
            self._save_record(best)
            return self._to_card(best)

    def finish(self, task_id: str, run_id: str, outcome: str) -> None:
        with file_lock(self._lock_path):
            record = self._load_record(task_id)
            run = next((r for r in record["runs"] if r["run_id"] == run_id), None)
            if run is None:
                raise ValueError(f"card {task_id} has no run {run_id}")
            if run["terminal"]:
                if run["outcome"] == outcome and record["state"] == "completed":
                    return
                raise ValueError(f"run {run_id} already finished with outcome {run['outcome']!r}")
            run["terminal"] = True
            run["outcome"] = outcome
            record["state"] = "completed"
            self._save_record(record)

    @staticmethod
    def _require_pending(record: dict, action: str) -> None:
        if record["state"] != "pending":
            raise ValueError(f"cannot {action} a {record['state']} card; only pending cards")
