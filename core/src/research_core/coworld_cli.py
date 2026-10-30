"""``research coworld switch <id>`` and ``research coworld setup-status``.

The bridge's ``store apply`` allow-list delegates ``workspace.coworld_switch`` to the first
(amendment A2.10) and the setup wake reads the second. Both run in the workspace environment
and print one JSON document with workspace paths scrubbed, as the tool does (design B.10).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _context():
    from research_core.tools.base import ToolContext

    return ToolContext.from_env("orchestrator", dict(os.environ, IDE_ROLE="orchestrator"))


def _emit(ctx, payload: dict) -> None:
    print(ctx.scrub(json.dumps(payload, default=str)))
    sys.stdout.flush()


def run_switch(args: argparse.Namespace) -> int:
    from research_core import coworld_setup
    from research_core.store.errors import StoreError
    from research_core.tools.base import ToolError

    ctx = _context()
    try:
        result = coworld_setup.switch(
            ctx, args.coworld_id, cites=args.cites, source=args.source, commit=args.commit
        )
    except (ToolError, StoreError) as error:
        _emit(ctx, {"ok": False, "error": str(error), "problems": list(error.problems)})
        return 1
    _emit(ctx, {"ok": True, **result})
    return 0


def run_setup_status(args: argparse.Namespace) -> int:
    from research_core import coworld_setup

    ctx = _context()
    _emit(ctx, {"ok": True, **coworld_setup.setup_status(ctx)})
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "coworld", help="Switch the active Coworld or report where its setup stands"
    )
    commands = parser.add_subparsers(dest="coworld_command", metavar="<subcommand>")
    switch = commands.add_parser(
        "switch",
        help="Register and activate a Coworld; fetch its source and emit coworld.setup_needed",
    )
    switch.add_argument("coworld_id", help="The platform Coworld id, such as cow_fake")
    switch.add_argument("--cites", default=None, help="The user's message reference")
    switch.add_argument("--source", default=None, help="Override the manifest's source URL")
    switch.add_argument("--commit", default=None, help="Override the pinned source commit")
    switch.set_defaults(run=run_switch)
    status = commands.add_parser("setup-status", help="The active Coworld's setup status as JSON")
    status.set_defaults(run=run_setup_status)
