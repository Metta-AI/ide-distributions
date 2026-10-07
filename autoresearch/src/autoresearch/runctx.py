"""Run context resolved from the environment. STDLIB ONLY: the Hermes plugin imports this."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# If Hermes' real env var names differ (Task 0.2 findings), change them here only.
ENV_HERMES_HOME = "HERMES_HOME"
ENV_CANONICAL = "AUTORESEARCH_WORKSPACE"
ENV_WORKER = "HERMES_KANBAN_WORKSPACE"
ENV_CARD_ID = "HERMES_KANBAN_TASK"
ENV_RUN_ID = "HERMES_KANBAN_RUN"

SERIAL_CURRENT_RELATIVE = Path(".supervisor/runtime/serial_current.json")


@dataclass(frozen=True)
class RunContext:
    hermes_home: Path
    canonical: Path
    worker: Path | None
    card_id: str | None
    run_id: str | None


def _read_serial_current(canonical: Path) -> dict:
    path = canonical / SERIAL_CURRENT_RELATIVE
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def from_env(env: Mapping[str, str] = os.environ) -> RunContext:
    hermes_home = Path(env[ENV_HERMES_HOME])
    canonical = Path(env.get(ENV_CANONICAL) or hermes_home / "workspace")
    worker = env.get(ENV_WORKER)
    card_id = env.get(ENV_CARD_ID)
    run_id = env.get(ENV_RUN_ID)
    if card_id is None:
        serial = _read_serial_current(canonical)
        card_id = serial.get("task_id")
        run_id = serial.get("run_id", run_id)
        worker = serial.get("worker", worker)
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
