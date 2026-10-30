"""``research watcher`` (``tick``, ``status``) and ``research workspace`` (``sleep``, ``wake``,
``drain``).

``tick`` is what the Orchestrator's cron script runs every minute
(``orchestrator/scripts/watcher.py`` starts it in the workspace environment); ``status``
prints the last heartbeat and the has-work signal from the workspace store for a person or
``workspace.doctor``. ``workspace sleep`` and ``workspace wake`` are how the host starts and
ends a sleep cycle (``watcher/sleep.py``; ``docs/hosting.md`` "Gateway, sleep and drain");
``workspace drain`` is the raw drain step on its own. Every result is one JSON line.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime

from research_core import layout
from research_core.store import settings
from research_core.store._common import parse_iso
from research_core.store.db import Store
from research_core.watcher import context
from research_core.watcher import tick as tick_module
from research_core.watcher.tick import PAUSED_DRAINED_KEY

DRAIN_MODULE = "research_core.watcher.drain"
NO_HERMES_HOME = (
    "HERMES_HOME is not set (the Orchestrator profile home; the workspace commands stop and"
    " poll runs through its runs API)"
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    watcher = subparsers.add_parser(
        "watcher", help="The Orchestrator's model-less cron job: run one tick, or its status"
    )
    commands = watcher.add_subparsers(dest="watcher_command", metavar="<subcommand>")
    commands.required = True
    tick = commands.add_parser("tick", help="Run one watcher tick and print its JSON summary")
    tick.set_defaults(run=run_tick)
    status = commands.add_parser(
        "status", help="Print the last heartbeat, the has-work signal and the pause state"
    )
    status.set_defaults(run=run_status)

    workspace = subparsers.add_parser(
        "workspace", help="Workspace operations: start and end a sleep cycle, or drain"
    )
    workspace_commands = workspace.add_subparsers(dest="workspace_command", metavar="<subcommand>")
    workspace_commands.required = True
    sleep = workspace_commands.add_parser(
        "sleep",
        help="Get ready for the host to stop the container: pause, stop every run, flush to disk",
        description=(
            "Pause the workspace, drain Researcher runs and processes, let the Orchestrator's"
            " runs finish (stopping them after --wait), then checkpoint and fsync every database"
            " and sync the volume. Exit 0: nothing runs and every write is on disk. Exit 1:"
            " 'volatile' lists what is still running or not flushed."
        ),
    )
    sleep.add_argument(
        "--wait",
        type=float,
        default=120.0,
        help="Seconds to let the Orchestrator's chat and wake runs finish before stopping them",
    )
    sleep.set_defaults(run=run_sleep)
    wake = workspace_commands.add_parser(
        "wake",
        help="End a sleep: clear the pause sleep set; the next tick resumes the drained work",
    )
    wake.set_defaults(run=run_wake)
    drain = workspace_commands.add_parser(
        "drain",
        help="Stop running Researcher runs and background processes; they resume after the wake",
    )
    drain.set_defaults(run=run_drain)


def run_tick(args: argparse.Namespace) -> int:
    return tick_module.main([])


def run_status(args: argparse.Namespace) -> int:
    ws = layout.from_env(os.environ)
    if not ws.store_path.exists():
        print(json.dumps({"ok": False, "error": "no workspace store yet; run prepare.py"}))
        return 1
    with Store.open(ws.store_path, kind="workspace") as wstore:
        heartbeat = wstore.get_meta("heartbeat")
        age_s = None
        if heartbeat is not None:
            age_s = round((datetime.now(UTC) - parse_iso(heartbeat)).total_seconds(), 1)
        payload = {
            "ok": True,
            "heartbeat": heartbeat,
            "heartbeat_age_s": age_s,
            "has_work": wstore.get_meta("has_work") == "1",
            "waiting_on": json.loads(wstore.get_meta("has_work_reasons") or "[]"),
            "paused": bool(settings.get(wstore, "paused")),
            "paused_drained": wstore.get_meta(PAUSED_DRAINED_KEY) == "1",
            "sleep_started": wstore.get_meta("sleep_started"),
        }
    print(json.dumps(payload))
    return 0


def run_drain(args: argparse.Namespace) -> int:
    try:
        drain = importlib.import_module(DRAIN_MODULE)
    except ModuleNotFoundError as error:
        if error.name != DRAIN_MODULE:
            raise
        print(json.dumps({"ok": False, "error": "workspace drain is not built yet"}))
        return 1
    if not os.environ.get("HERMES_HOME"):
        print(json.dumps({"ok": False, "error": NO_HERMES_HOME}))
        return 1
    ctx = context.build(os.environ)
    try:
        result = drain.run(ctx)
    finally:
        close_stores(ctx)
    print(json.dumps({"ok": True, **(result if isinstance(result, dict) else {})}, default=str))
    return 0


def run_sleep(args: argparse.Namespace) -> int:
    from research_core.watcher import sleep

    if not os.environ.get("HERMES_HOME"):
        print(json.dumps({"ok": False, "error": NO_HERMES_HOME}))
        return 1
    if args.wait < 0:
        print(json.dumps({"ok": False, "error": "--wait must be zero or more seconds"}))
        return 1
    ctx = context.build(os.environ)
    try:
        report = sleep.go_to_sleep(ctx, wait_s=args.wait)
    finally:
        close_stores(ctx)
    try:
        flushed = sleep.flush_everything(ctx.ws, os.environ, ctx.researcher_profiles)
    except (sqlite3.Error, OSError, subprocess.SubprocessError) as error:
        flushed = {"error": f"{type(error).__name__}: {error}"}
    volatile = sleep.volatile(report["still_running"], flushed)
    payload: dict = {"ok": not volatile, "paused": True, **report, "flushed": flushed}
    payload["volatile"] = volatile
    if volatile:
        payload["error"] = "not ready to stop: see volatile"
    print(json.dumps(payload, default=str))
    return 0 if not volatile else 1


def run_wake(args: argparse.Namespace) -> int:
    from research_core.watcher import sleep

    if not os.environ.get("HERMES_HOME"):
        print(json.dumps({"ok": False, "error": NO_HERMES_HOME}))
        return 1
    ctx = context.build(os.environ)
    try:
        result = sleep.wake_up(ctx)
    finally:
        close_stores(ctx)
    print(json.dumps({"ok": True, **result}, default=str))
    return 0


def close_stores(ctx: context.TickContext) -> None:
    ctx.wstore.close()
    if ctx.store is not None:
        ctx.store.close()
