"""``research store``: user edits, exports, snapshots and the event stream (design A.10, A.11).

``apply`` is how a user edit reaches the store. The bridge turns a UI action into
``research store apply --actor user:<id> <op> <json>``, which calls the same library function
the tools call, so the record rules, the ``events`` row and the token-marked transaction are the
same whoever writes. Only the ops in ``ALLOWED`` run, and only for user or harness actors:
sessions use the tools. The reply carries the store's ``seq`` after the write, which is what
"accepted on commit" means to a client (A.11). ``snapshot`` and ``events`` are the read side of
the seam: a client loads a snapshot, then streams from its ``seq``; each store has its own
sequence, so both commands work on one store at a time. ``export`` dumps every table of both
stores as JSON lines for backups and debugging.

Every result is one JSON line on stdout: ``{"ok": true, ...}`` or ``{"ok": false, "error",
"problems"}`` with exit code 1. The commands never show a traceback.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from research_core import layout
from research_core.store import coworlds, directives, goals, settings, tasks, ui_state
from research_core.store._common import parse_display_id, require
from research_core.store.actors import Actor
from research_core.store.db import Store
from research_core.store.errors import StoreError
from research_core.store.migrate import _copyable_tables
from research_core.store.registry import RECORDS, RecordSpec, record_from_row

# The user edits of design A.10 plus amendment A2.10. ``<module>.<function>`` names the library
# function; the ``workspace.*`` ops are small compositions defined in ``_call``.
ALLOWED: frozenset[str] = frozenset(
    {
        "goals.set_status",
        "goals.update",
        "tasks.approve",
        "tasks.update",
        "tasks.reject",
        "tasks.abandon",
        "settings.update",
        "directives.add",
        "directives.update",
        "directives.retire",
        "workspace.pause",
        "workspace.resume",
        "workspace.coworld_switch",
    }
)
LIBRARY_MODULES = {"goals": goals, "tasks": tasks, "settings": settings, "directives": directives}
STORE_KINDS = ("workspace", "coworld")
# Which record kind each id argument names, so ``G-3`` becomes 3 for ``goal_id`` and a Task id
# given as ``goal_id`` is refused instead of silently reading the wrong table.
ID_ARGUMENTS = {"goal_id": "goal", "task_id": "task", "directive_id": "directive"}
NO_ACTIVE_COWORLD = (
    "no active Coworld; pass --store workspace, or switch to a Coworld first"
    " (workspace.coworld_switch)"
)
OUTPUT_TAIL_CHARS = 2000


class Refusal(Exception):
    """A result the command prints as ``{"ok": false, "error", "problems"}``."""

    def __init__(self, error: str, problems: Sequence[str] = ()) -> None:
        self.error = error
        self.problems = list(problems)
        super().__init__(error)


class Stores:
    """The workspace store and, opened on first use, the active (or named) Coworld's store."""

    def __init__(self, ws: layout.Workspace, wstore: Store, coworld_id: str | None) -> None:
        self.ws = ws
        self.wstore = wstore
        self.coworld_id = coworld_id
        self._store: Store | None = None

    @classmethod
    def open(cls, coworld_id: str | None = None) -> Stores:
        """Open the workspace store under ``IDE_WORKSPACE``; the Coworld is the named or active."""
        ws = layout.from_env()
        wstore = Store.open(ws.store_path, kind="workspace")
        if coworld_id is None:
            active = coworlds.active(wstore)
            coworld_id = active["coworld_id"] if active else None
        return cls(ws, wstore, coworld_id)

    @property
    def coworld_store_path(self) -> Path | None:
        return None if self.coworld_id is None else self.ws.coworld(self.coworld_id).store_path

    def store(self) -> Store:
        if self._store is None:
            if self.coworld_store_path is None:
                raise StoreError([NO_ACTIVE_COWORLD])
            self._store = Store.open(self.coworld_store_path, kind="coworld")
        return self._store

    def by_kind(self, kind: str) -> Store:
        return self.wstore if kind == "workspace" else self.store()

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
        self.wstore.close()

    def __enter__(self) -> Stores:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _fail(error: str, problems: Sequence[str] = ()) -> int:
    print(json.dumps({"ok": False, "error": error, "problems": list(problems)}))
    return 1


# --- apply ----------------------------------------------------------------------------------


def run_apply(args: argparse.Namespace) -> int:
    op = args.op
    try:
        actor = parse_actor(args.actor)
        if op not in ALLOWED:
            raise Refusal(
                f"{op!r} is not an op store apply runs; allowed: {', '.join(sorted(ALLOWED))}"
            )
        op_args = parse_op_args(args.args)
        with Stores.open() as stores:
            kind = args.store or store_kind_for(op, op_args, stores)
            record = _call(op, kind, stores, actor, op_args)
            seq = stores.by_kind(kind).last_seq()
    except Refusal as error:
        return _fail(error.error, error.problems)
    except StoreError as error:
        return _fail(f"{op} refused", error.problems)
    except Exception as error:  # the bridge reads JSON, never a traceback
        return _fail(f"{op} failed: {type(error).__name__}: {error}")
    result = {"ok": True, "op": op, "store": kind, "record": record, "seq": seq}
    print(json.dumps(result, ensure_ascii=False))
    return 0


def parse_actor(text: str) -> Actor:
    try:
        actor = Actor.parse(text)
    except ValueError as error:
        raise Refusal(str(error)) from None
    if actor.is_session:
        raise Refusal("sessions use the tools; store apply is for user:<id> and harness actors")
    return actor


def parse_op_args(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError as error:
        raise Refusal(f"the op arguments are not JSON: {error}") from None
    if not isinstance(parsed, dict):
        raise Refusal("the op arguments must be a JSON object of the function's arguments")
    problems: list[str] = []
    for name, kind in ID_ARGUMENTS.items():
        if isinstance(parsed.get(name), str):
            parsed[name] = _record_number(parsed[name], name, kind, problems)
    if problems:
        raise StoreError(problems)
    return parsed


def _record_number(value: str, name: str, kind: str, problems: list[str]) -> Any:
    """``"G-3"`` → 3 for ``goal_id``; another kind's id is a problem; other text passes through."""
    try:
        found_kind, number = parse_display_id(value)
    except ValueError:
        return value
    if found_kind != kind:
        problems.append(f"{name} is {value!r}, a {found_kind} id; expected a {kind} id")
        return value
    return number


def store_kind_for(op: str, op_args: dict[str, Any], stores: Stores) -> str:
    """Which store the op writes: settings by the key's scope, directives by their scope or
    home, Goals and Tasks in the active Coworld, the ``workspace.*`` ops in the workspace."""
    module = op.split(".", 1)[0]
    if module in ("goals", "tasks"):
        return "coworld"
    if module == "workspace":
        return "workspace"
    if op == "settings.update":
        key = op_args.get("key")
        if not isinstance(key, str):
            raise StoreError(["key is required: the setting to change"])
        return settings.spec(key).scope
    if op == "directives.add":
        return "workspace" if op_args.get("scope") == directives.WORKSPACE_SCOPE else "coworld"
    return _directive_home(stores, op_args.get("directive_id"))


def _directive_home(stores: Stores, directive_id: Any) -> str:
    """The store holding an active ``D-<n>`` (else any holding it, so the library's own
    "retired" refusal shows). Both stores number directives independently, so when both have
    an active one the caller must say which with ``--store``."""
    if not isinstance(directive_id, int) or isinstance(directive_id, bool):
        raise StoreError(["directive_id is required: an integer or D-<n>"])
    kinds = ["workspace", "coworld"] if stores.coworld_id else ["workspace"]
    statuses = {
        kind: stores.by_kind(kind).one(
            "SELECT status FROM directives WHERE id = ?", (directive_id,)
        )
        for kind in kinds
    }
    homes = [kind for kind, row in statuses.items() if row and row["status"] == "active"]
    if not homes:
        homes = [kind for kind, row in statuses.items() if row]
    if len(homes) > 1:
        raise StoreError(
            [
                f"D-{directive_id} exists in both the workspace and the Coworld store;"
                " pass --store workspace or --store coworld"
            ]
        )
    if not homes:
        raise StoreError([f"D-{directive_id} does not exist"])
    return homes[0]


def _call(op: str, kind: str, stores: Stores, actor: Actor, op_args: dict[str, Any]) -> Any:
    store = stores.by_kind(kind)
    if op == "workspace.coworld_switch":
        return _coworld_switch(actor, op_args)
    if op in ("workspace.pause", "workspace.resume"):
        # The op's only argument is cites; the library says what it is when it is missing.
        unknown = sorted(set(op_args) - {"cites"})
        if unknown:
            raise Refusal(
                f"arguments do not fit {op}(cites): unknown arguments: {', '.join(unknown)}"
            )
        paused = op == "workspace.pause"
        return settings.update(store, actor, "paused", paused, cites=op_args.get("cites"))
    module, name = op.split(".", 1)
    function = getattr(LIBRARY_MODULES[module], name)
    bound = _bind(op, function, (store, actor), op_args)
    return function(*bound.args, **bound.kwargs)


def _bind(
    op: str, function: Callable[..., Any], leading: tuple, op_args: dict[str, Any]
) -> inspect.BoundArguments:
    """Match the JSON arguments to the function before calling it, so a missing or unknown
    argument is a refusal that shows the arguments the op takes."""
    signature = inspect.signature(function)
    try:
        return signature.bind(*leading, **op_args)
    except TypeError as error:
        shown = inspect.Signature(list(signature.parameters.values())[len(leading) :])
        raise Refusal(f"arguments do not fit {op}{shown}: {error}") from None


def _coworld_switch(actor: Actor, op_args: dict[str, Any]) -> dict[str, Any]:
    """Delegate to ``research coworld switch`` in the workspace environment (A2.10): the switch
    fetches the Coworld's source and mixin, which needs the platform client."""
    problems: list[str] = []
    coworld = op_args.get("coworld")
    cites = op_args.get("cites")
    require(
        problems,
        isinstance(coworld, str) and coworld.strip(),
        "coworld is required: the Coworld id to switch to",
    )
    require(
        problems,
        cites is None or (isinstance(cites, str) and cites.strip()),
        "cites must be the reference of the user's message",
    )
    unknown = sorted(set(op_args) - {"coworld", "cites"})
    require(problems, not unknown, f"unknown arguments: {', '.join(unknown)}")
    if problems:
        raise StoreError(problems)
    argv = [sys.executable, "-m", "research_core.cli", "coworld", "switch", coworld]
    if cites:
        argv += ["--cites", cites]
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "IDE_ACTOR": str(actor)},
    )
    if done.returncode != 0:
        tails = [done.stderr.strip()[-OUTPUT_TAIL_CHARS:], done.stdout.strip()[-OUTPUT_TAIL_CHARS:]]
        raise StoreError(
            [f"research coworld switch {coworld} exited {done.returncode}", *filter(None, tails)]
        )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    try:
        record = json.loads(lines[-1]) if lines else None
    except ValueError:
        record = None
    if isinstance(record, dict):
        return record
    return {"output": done.stdout.strip()[-OUTPUT_TAIL_CHARS:]}


# --- export ---------------------------------------------------------------------------------


def run_export(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    try:
        with Stores.open(args.coworld) as stores:
            if args.coworld and not stores.coworld_store_path.exists():
                raise StoreError([f"Coworld {args.coworld} has no store in this workspace"])
            summary: dict[str, Any] = {"out": str(out_dir), "stores": {}}
            summary["stores"]["workspace"] = _export_store(stores.wstore, out_dir / "workspace")
            if stores.coworld_id:
                folder = out_dir / stores.coworld_id
                summary["stores"][stores.coworld_id] = _export_store(stores.store(), folder)
    except (StoreError, OSError) as error:
        return _fail(str(error), getattr(error, "problems", []))
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False))
    return 0


def _export_store(store: Store, folder: Path) -> dict[str, Any]:
    """One ``<table>.jsonl`` per ordinary table (FTS indexes are derived; they are left out)."""
    folder.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in _copyable_tables(store.conn, "main"):
        rows = store.q(f"SELECT * FROM {table} ORDER BY rowid")
        with (folder / f"{table}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        counts[table] = len(rows)
    return {"path": str(folder), "seq": store.last_seq(), "tables": counts}


# --- snapshot -------------------------------------------------------------------------------


def run_snapshot(args: argparse.Namespace) -> int:
    try:
        with Stores.open() as stores:
            store = stores.by_kind(args.store)
            print(json.dumps(snapshot(store, stores.coworld_id), ensure_ascii=False))
    except StoreError as error:
        return _fail(str(error), error.problems)
    return 0


def snapshot(store: Store, coworld_id: str | None = None) -> dict[str, Any]:
    """The store's records, effective settings, UI state documents and last ``seq``.

    A client that loads this and then reads ``events --after <seq>`` misses nothing: both read
    the same store, and ``seq`` is taken from the same connection as the rows.
    """
    existing = set(_copyable_tables(store.conn, "main"))
    records: dict[str, Any] = {}
    for spec in RECORDS.values():
        if spec.table in existing:
            rows = store.q(f"SELECT * FROM {spec.table} ORDER BY rowid")
            records[spec.table] = [_record(spec, row) for row in rows]
    records["settings"] = settings.all(store)
    conversations = [
        row[0] for row in store.q("SELECT conversation FROM ui_state ORDER BY conversation")
    ]
    result: dict[str, Any] = {"store": store.kind}
    if store.kind == "coworld":
        result["coworld_id"] = coworld_id
    result["seq"] = store.last_seq()
    result["records"] = records
    result["ui_state"] = [ui_state.get(store, conversation) for conversation in conversations]
    return result


def _record(spec: RecordSpec, row: Any) -> dict[str, Any]:
    record = record_from_row(spec, row)
    for field in spec.stored_fields:
        if field.type == "bool" and record.get(field.name) is not None:
            record[field.name] = bool(record[field.name])
    return record


# --- events ---------------------------------------------------------------------------------


def run_events(args: argparse.Namespace) -> int:
    try:
        with Stores.open() as stores:
            store = stores.by_kind(args.store)
            return _print_events(store, args)
    except StoreError as error:
        return _fail(str(error), error.problems)
    except KeyboardInterrupt:
        return 0


def _print_events(store: Store, args: argparse.Namespace) -> int:
    """Rows after ``--after`` as JSON lines; ``--follow`` keeps polling every ``--interval``
    seconds until SIGINT or, for tests and scripts, until ``--max-rows`` rows were printed."""
    cursor = args.after
    printed = 0
    while True:
        for event in store.events_after(cursor, args.limit):
            print(
                json.dumps({"store": store.kind, **event.to_dict()}, ensure_ascii=False), flush=True
            )
            cursor = event.seq
            printed += 1
            if args.max_rows is not None and printed >= args.max_rows:
                return 0
        if not args.follow:
            return 0
        time.sleep(args.interval)


# --- the parser -----------------------------------------------------------------------------


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "store",
        help="User edits (apply), exports, snapshots and the event stream of the stores",
        description="The store side of the user-edit route and the seam (design A.10, A.11).",
    )
    commands = parser.add_subparsers(dest="store_command", metavar="<command>", required=True)

    apply_parser = commands.add_parser(
        "apply",
        help="Run one allowed library function as a user or harness actor",
        description=(
            "Ops: " + ", ".join(sorted(ALLOWED)) + ". The arguments are a JSON object of the"
            " function's keyword arguments; record ids may be given as G-3, T-42 or D-2."
        ),
    )
    apply_parser.add_argument("--actor", required=True, help="user:<id> or harness")
    apply_parser.add_argument(
        "--store",
        choices=STORE_KINDS,
        default=None,
        help="Where the record lives, when the op cannot tell (a directive id in both stores)",
    )
    apply_parser.add_argument("op", metavar="<op>")
    apply_parser.add_argument("args", metavar="<json-args>", nargs="?", default="{}")
    apply_parser.set_defaults(run=run_apply)

    export_parser = commands.add_parser(
        "export", help="Write every table of the workspace and Coworld stores as JSON lines"
    )
    export_parser.add_argument("--coworld", default=None, help="A Coworld id; default: the active")
    export_parser.add_argument("--out", required=True, help="The output directory")
    export_parser.set_defaults(run=run_export)

    snapshot_parser = commands.add_parser(
        "snapshot", help="Print one store's records, settings, UI state and last seq as JSON"
    )
    snapshot_parser.add_argument("--store", choices=STORE_KINDS, default="coworld")
    snapshot_parser.set_defaults(run=run_snapshot)

    events_parser = commands.add_parser(
        "events", help="Print one store's events after a seq as JSON lines; --follow tails"
    )
    events_parser.add_argument("--after", type=int, required=True, help="The last seq seen")
    events_parser.add_argument("--store", choices=STORE_KINDS, default="coworld")
    events_parser.add_argument("--limit", type=int, default=500, help="Rows per read")
    events_parser.add_argument("--follow", action="store_true", help="Keep polling until SIGINT")
    events_parser.add_argument("--interval", type=float, default=0.5, help="Poll seconds")
    events_parser.add_argument(
        "--max-rows", type=int, default=None, help="Exit after this many rows (for scripts)"
    )
    events_parser.set_defaults(run=run_events)
