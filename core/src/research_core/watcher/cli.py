"""``research watcher`` (``tick``, ``status``) and ``research workspace drain``.

``tick`` is what the Orchestrator's cron script runs every minute
(``orchestrator/scripts/watcher.py`` starts it in the workspace environment); ``status``
prints the last heartbeat and the has-work signal from the workspace store for a person or
``workspace.doctor``; ``workspace drain`` is what the hosting controller calls at the start of
an explicit pause (design Appendix H "Sleep and drain"). Every result is one JSON line.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import UTC, datetime

from research_core import layout
from research_core.store import settings
from research_core.store._common import parse_iso
from research_core.store.db import Store
from research_core.watcher import context
from research_core.watcher import tick as tick_module
from research_core.watcher.tick import PAUSED_DRAINED_KEY

DRAIN_MODULE = "research_core.watcher.drain"


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
        "workspace", help="Workspace operations: drain running Researcher work before a pause"
    )
    workspace_commands = workspace.add_subparsers(dest="workspace_command", metavar="<subcommand>")
    workspace_commands.required = True
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
            "paused": bool(settings.get(wstore, "paused")),
            "paused_drained": wstore.get_meta(PAUSED_DRAINED_KEY) == "1",
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
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "HERMES_HOME is not set (the Orchestrator"
                    " profile home; the drain stops runs through its runs API)",
                }
            )
        )
        return 1
    ctx = context.build(os.environ)
    try:
        result = drain.run(ctx)
    finally:
        ctx.wstore.close()
        if ctx.store is not None:
            ctx.store.close()
    print(json.dumps({"ok": True, **(result if isinstance(result, dict) else {})}, default=str))
    return 0
