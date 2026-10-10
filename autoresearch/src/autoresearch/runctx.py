"""Run context resolved from the environment. STDLIB ONLY: the Hermes plugin imports this.

Worker identity. Hermes v0.21.5 removes ``HERMES_KANBAN_TASK`` and ``HERMES_KANBAN_RUN_ID``
from every subprocess of a Kanban worker (``agent/delegation_context.py``
``scrub_kanban_env``), so a terminal command such as ``"$AUTORESEARCH_CLI" card finish`` does
not see them; ``HERMES_KANBAN_WORKSPACE`` does survive. The plugin, which runs inside the
worker process, records the identity on the worker's first turn
(``record_worker_identity``) in ``runtime/workers/<worktree name>.json``, and ``from_env``
reads it back for a worktree when the variables are missing. Model writes there are blocked
by the plugin's write policy (outside the worktree).
"""

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# If Hermes' real env var names differ (Task 0.2 findings), change them here only.
ENV_HERMES_HOME = "HERMES_HOME"
ENV_CANONICAL = "AUTORESEARCH_WORKSPACE"
ENV_WORKER = "HERMES_KANBAN_WORKSPACE"
ENV_CARD_ID = "HERMES_KANBAN_TASK"
ENV_RUN_ID = "HERMES_KANBAN_RUN_ID"
# Set to "1" by the plugin in every process it loads into (Hermes agent sessions: card workers,
# chat); their terminals and code inherit it. Operator-only commands refuse when it is set.
# Cooperative like any environment variable; the plugin also refuses commands that name it.
ENV_AGENT_SESSION = "AUTORESEARCH_AGENT_SESSION"

WORKERS_RELATIVE = Path(".supervisor/runtime/workers")

# The Kanban board slug. Fixed: the profile's install name varies (``optimizer`` on Softmax
# hosting, ``autoresearch`` in a manual install), the board does not (plan D2).
BOARD = "autoresearch"


@dataclass(frozen=True)
class RunContext:
    hermes_home: Path
    canonical: Path
    worker: Path | None
    card_id: str | None
    run_id: str | None

    @property
    def profile(self) -> str:
        """The install name of this profile (the card assignee): the profile home's name."""
        return self.hermes_home.name


def worker_identity_path(canonical: Path, worker: Path) -> Path:
    return canonical / WORKERS_RELATIVE / f"{Path(worker).name}.json"


def _read_worker_identity(canonical: Path, worker: Path) -> dict:
    path = worker_identity_path(canonical, worker)
    if not path.exists():
        return {}
    record = json.loads(path.read_text())
    return record if record.get("worker") == str(worker) else {}


def record_worker_identity(ctx: "RunContext") -> None:
    """Record the card and run of this worker for its subprocesses (module docstring),
    replacing any earlier run's record. If the write fails, the earlier record is removed
    before the error is raised, so a retried run never inherits the previous run id (its
    commands then fail for lack of card context instead)."""
    if ctx.worker is None or not ctx.card_id or not ctx.run_id:
        return
    path = worker_identity_path(ctx.canonical, ctx.worker)
    record = {
        "task_id": ctx.card_id,
        "run_id": ctx.run_id,
        "worker": str(ctx.worker),
        "started_at": time.time(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        os.replace(temporary, path)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def from_env(env: Mapping[str, str] = os.environ) -> RunContext:
    hermes_home = Path(env[ENV_HERMES_HOME])
    canonical = Path(env.get(ENV_CANONICAL) or hermes_home / "workspace")
    worker = env.get(ENV_WORKER)
    card_id = env.get(ENV_CARD_ID)
    run_id = env.get(ENV_RUN_ID)
    if card_id is None and worker:
        recorded = _read_worker_identity(canonical, Path(worker))
        card_id = recorded.get("task_id")
        run_id = recorded.get("run_id", run_id)
    return RunContext(
        hermes_home=hermes_home,
        canonical=canonical,
        worker=Path(worker) if worker else None,
        card_id=card_id,
        run_id=run_id,
    )


def runtime_dir(ctx: RunContext) -> Path:
    """Disposable, gitignored state (locks, briefings, caches)."""
    return ctx.canonical / ".supervisor" / "runtime"


def ledger_dir(ctx: RunContext) -> Path:
    """Durable, committed bookkeeping (handoffs, receipts, promotion state)."""
    return ctx.canonical / ".supervisor" / "ledger"
