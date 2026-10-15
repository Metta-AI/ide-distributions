"""Command-line entry point.

Later tasks add subcommand groups with `register_group(name, setup_fn)`, where
`setup_fn(subparser)` adds arguments and sets `func=<handler>` as a parser default.
A handler takes the parsed `argparse.Namespace` and returns an exit code. A group module
calls `register_group` at import time; list it in `GROUP_MODULES` so the parser imports it.
A group option named `--root` (dest `root`) is made absolute here, before any handler runs.
"""

import argparse
import importlib
import os
from collections.abc import Callable, Sequence

from ide_research_core import __version__

GroupSetup = Callable[[argparse.ArgumentParser], None]

_GROUPS: dict[str, GroupSetup] = {}

GROUP_MODULES = (
    "ide_research_core.records.cli",
    "ide_research_core.stats.cli",
    "ide_research_core.kb.cli",
    "ide_research_core.runtime.card_cli",
    "ide_research_core.comms.cli",
    "ide_research_core.queue.cli",
    "ide_research_core.runtime.promotion_cli",
    "ide_research_core.runtime.provenance_cli",
    "ide_research_core.softmax_tool.cli",
    "ide_research_core.games.cli",
    "ide_research_core.runtime.status_cli",
    "ide_research_core.runtime.report_cli",
)


def register_group(name: str, setup_fn: GroupSetup) -> None:
    _GROUPS[name] = setup_fn


def build_parser() -> argparse.ArgumentParser:
    for module in GROUP_MODULES:
        importlib.import_module(module)
    parser = argparse.ArgumentParser(prog="autoresearch")
    parser.add_argument("--version", action="version", version=f"autoresearch {__version__}")
    subparsers = parser.add_subparsers(dest="group", metavar="GROUP")
    for name, setup_fn in _GROUPS.items():
        setup_fn(subparsers.add_parser(name))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # argparse exits with code 2 on bad arguments
    if getattr(args, "root", None):
        # Every group's --root (records, stats, kb, comms). Handlers pass paths under it to
        # tools that run in another directory (``git -C``), so a relative root breaks them.
        args.root = os.path.abspath(args.root)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)
