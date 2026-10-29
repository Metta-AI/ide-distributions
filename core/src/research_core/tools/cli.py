"""``research tool <name> --args <json>``: run one tool call in the workspace environment.

The plugin's workspace executor starts this for tools that need the Softmax client or numeric
libraries. The role comes from ``IDE_ROLE`` and the Hermes session from ``IDE_SESSION_ID``;
the last line of stdout is the JSON result that Hermes shows the model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def run(args: argparse.Namespace) -> int:
    from research_core.tools.base import ToolContext, run_tool

    try:
        tool_args = json.loads(args.args) if args.args else {}
    except ValueError as error:
        print(json.dumps({"ok": False, "error": f"--args is not JSON: {error}", "problems": []}))
        return 0
    role = args.role or os.environ.get("IDE_ROLE") or "orchestrator"
    ctx = ToolContext.from_env(role, dict(os.environ, IDE_ROLE=role))
    sys.stdout.flush()
    print(run_tool(args.name, tool_args, ctx))
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "tool", help="Run one profile tool call and print its JSON result (used by the plugin)"
    )
    parser.add_argument("name")
    parser.add_argument("--args", default="{}", help="The tool arguments as JSON")
    parser.add_argument("--role", choices=["orchestrator", "researcher"], default=None)
    parser.set_defaults(run=run)
