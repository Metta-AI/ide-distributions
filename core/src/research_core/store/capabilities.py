"""Capabilities (design B.15): one row per checked capability, marked ok, broken or unknown.

A capability is a mixin command (build, check, upload, evaluate, game tools), a platform login
or CLI, GitHub access or a build route. ``mark`` records a check's result and emits
``capability.failed`` when the status becomes ``broken`` from anything else (the first failure
of a never-checked capability counts: a repair must be opened) and ``capability.repaired`` when
``broken`` becomes ``ok``. A repeat failure emits nothing: repeats join the open repair Task.

``report_failure`` is the entry point for tool failures (A4.1): it marks the capability broken
and finds or creates the one ``high`` priority ``maintenance`` Task repairing it. The Task comes
from ``store.tasks`` (imported when called, so this module loads without it); when that module
is absent the capability is still marked and the result says ``maintenance_task: None``. The
rest of A4.1 (dependencies for Tasks that need the capability, ``repair.failed_twice`` and its
notice) belongs to the platform and watcher entries.

Rows live in the Coworld store: the capabilities are the Coworld's commands and routes.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from research_core.store._common import display_id, parse_display_id, require
from research_core.store.actors import Actor
from research_core.store.db import Store, Tx
from research_core.store.errors import NotFound, StoreError
from research_core.store.registry import CAPABILITY_STATUSES, RECORDS, record_from_row

SPEC = RECORDS["capability"]
# Event payloads and the repair Task carry the end of the output; the row keeps all of it.
OUTPUT_TAIL_CHARS = 2000
# A repair Task in one of these statuses is still open, so a repeat failure joins it.
OPEN_TASK_STATUSES = ("proposed", "unclaimed", "in-progress")


def mark(
    store: Store,
    actor: Actor,
    name: str,
    *,
    status: str,
    output: str,
    command: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Record a check: upsert the row, emit ``capability.failed`` or ``capability.repaired``.

    ``command`` and ``kind`` are kept from the previous mark when not given.
    """
    problems: list[str] = []
    require(problems, _is_text(name), "name is a non-empty string")
    require(
        problems, status in CAPABILITY_STATUSES, f"status is one of {_list(CAPABILITY_STATUSES)}"
    )
    require(problems, isinstance(output, str), "output is the check's output (a string)")
    require(problems, command is None or _is_text(command), "command is a string or None")
    require(problems, kind is None or _is_text(kind), "kind is a string or None")
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        return _mark_in_tx(tx, name, status=status, output=output, command=command, kind=kind)


def set_maintenance_task(
    store: Store, actor: Actor, name: str, task_id: int | None
) -> dict[str, Any]:
    """Link (or with ``None`` unlink) the maintenance Task repairing the capability."""
    problems: list[str] = []
    require(problems, _is_text(name), "name is a non-empty string")
    require(problems, task_id is None or _is_int(task_id), "task_id is an integer or None")
    if problems:
        raise StoreError(problems)
    with store.tx(actor) as tx:
        _require_capability(tx, name)
        if task_id is not None and tx.one("SELECT 1 FROM tasks WHERE id = ?", (task_id,)) is None:
            raise NotFound([f"{display_id('T', task_id)} does not exist"])
        tx.execute(
            "UPDATE capabilities SET maintenance_task_id = ? WHERE name = ?", (task_id, name)
        )
        return _record(tx, name)


def read(store: Store, name: str) -> dict[str, Any] | None:
    row = store.one("SELECT 1 FROM capabilities WHERE name = ?", (name,))
    return None if row is None else _record(store, name)


def status(store: Store) -> list[dict[str, Any]]:
    """Every capability row, by name."""
    rows = store.q("SELECT * FROM capabilities ORDER BY name")
    return [record_from_row(SPEC, row) for row in rows]


def broken(store: Store) -> list[str]:
    rows = store.q("SELECT name FROM capabilities WHERE status = 'broken' ORDER BY name")
    return [row["name"] for row in rows]


def report_failure(
    store: Store,
    actor: Actor,
    name: str,
    *,
    output: str,
    command: str | None,
    kind: str | None = None,
) -> dict[str, Any]:
    """A tool or check failed: mark the capability broken and find or create its repair Task.

    Returns the capability record plus ``maintenance_task`` (the Task record, or ``None`` when
    ``store.tasks`` is not available) and ``maintenance_task_created``.
    """
    record = mark(store, actor, name, status="broken", output=output, command=command, kind=kind)
    tasks = _tasks_module()
    if tasks is None:
        return {**record, "maintenance_task": None, "maintenance_task_created": False}
    task_id = _open_repair_task_id(store, name, record["maintenance_task_id"])
    created = task_id is None
    if created:
        task = tasks.create(
            store,
            actor,
            goal_id=None,
            type="maintenance",
            title=f"Repair the {name} capability",
            description=_repair_description(name, output, command),
            reasoning="Tasks that need this capability wait on its repair (design B.15).",
            for_human=f"The {name} capability is broken; repairing it comes before dependent work.",
            priority="high",
            capability=name,
        )
        task_id = parse_display_id(task["id"])[1]
    else:
        task = tasks.read(store, task_id)
    if record["maintenance_task_id"] != task_id:
        record = set_maintenance_task(store, actor, name, task_id)
    return {**record, "maintenance_task": task, "maintenance_task_created": created}


# --- helpers -----------------------------------------------------------------------------


def _mark_in_tx(
    tx: Tx, name: str, *, status: str, output: str, command: str | None, kind: str | None
) -> dict[str, Any]:
    row = tx.one("SELECT status FROM capabilities WHERE name = ?", (name,))
    previous = None if row is None else row["status"]
    if row is None:
        tx.execute(
            "INSERT INTO capabilities(name, kind, status, last_check, output, command)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, kind, status, tx.at, output, command),
        )
    else:
        tx.execute(
            "UPDATE capabilities SET status = ?, last_check = ?, output = ?,"
            " command = COALESCE(?, command), kind = COALESCE(?, kind) WHERE name = ?",
            (status, tx.at, output, command, kind, name),
        )
    record = _record(tx, name)
    payload = {
        "name": name,
        "from": previous,
        "to": status,
        "command": record["command"],
        "output_tail": output[-OUTPUT_TAIL_CHARS:],
    }
    if status == "broken" and previous != "broken":
        tx.event("capability.failed", name, payload)
    elif status == "ok" and previous == "broken":
        tx.event("capability.repaired", name, payload)
    return record


def _open_repair_task_id(store: Store, name: str, linked_task_id: int | None) -> int | None:
    """The open maintenance Task for ``name``: the linked one if still open, else by capability."""
    placeholders = ", ".join("?" for _ in OPEN_TASK_STATUSES)
    if linked_task_id is not None:
        row = store.one(
            f"SELECT 1 FROM tasks WHERE id = ? AND status IN ({placeholders})",
            (linked_task_id, *OPEN_TASK_STATUSES),
        )
        if row is not None:
            return linked_task_id
    row = store.one(
        "SELECT id FROM tasks WHERE type = 'maintenance' AND capability = ?"
        f" AND status IN ({placeholders}) ORDER BY id LIMIT 1",
        (name, *OPEN_TASK_STATUSES),
    )
    return None if row is None else row["id"]


def _repair_description(name: str, output: str, command: str | None) -> str:
    ran = f" running `{command}`" if command else ""
    tail = output[-OUTPUT_TAIL_CHARS:]
    return (
        f"The {name} capability failed{ran}.\n\n"
        f"Output (last {OUTPUT_TAIL_CHARS} characters; the capability record has all of it):\n"
        f"```\n{tail}\n```\n\n"
        "Diagnose the failure, fix it, and re-run the check so the capability is marked ok."
    )


def _tasks_module() -> ModuleType | None:
    try:
        return importlib.import_module("research_core.store.tasks")
    except ImportError:
        return None


def _require_capability(tx: Tx, name: str) -> None:
    if tx.one("SELECT 1 FROM capabilities WHERE name = ?", (name,)) is None:
        raise NotFound([f"capability {name} does not exist"])


def _record(reader: Store | Tx, name: str) -> dict[str, Any]:
    row = reader.one("SELECT * FROM capabilities WHERE name = ?", (name,))
    return record_from_row(SPEC, row)


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _list(values: tuple[str, ...]) -> str:
    return ", ".join(values)
