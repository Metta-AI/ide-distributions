"""``research context-schema``: print a contract schema for the bridge (design A.2)."""

from __future__ import annotations

import argparse
import json

from research_core import contract


def run(args: argparse.Namespace) -> int:
    print(json.dumps(contract.schema(args.name), indent=2))
    return 0


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "context-schema",
        help="Print a contract schema (context, events or ui-state) as JSON",
        description="The bridge reads the contract from the installed profile with this command.",
    )
    parser.add_argument("--name", choices=["context", "events", "ui-state"], default="context")
    parser.set_defaults(run=run)
