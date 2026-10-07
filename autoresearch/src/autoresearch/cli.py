"""Command-line entry point.

Later tasks add subcommand groups with `register_group(name, setup_fn)`, where
`setup_fn(subparser)` adds arguments and sets `func=<handler>` as a parser default.
A handler takes the parsed `argparse.Namespace` and returns an exit code. A group module
calls `register_group` at import time; list it in `GROUP_MODULES` so the parser imports it.
"""

import argparse
import importlib
from collections.abc import Callable, Sequence

from autoresearch import __version__

GroupSetup = Callable[[argparse.ArgumentParser], None]

_GROUPS: dict[str, GroupSetup] = {}

GROUP_MODULES = (
    "autoresearch.records.cli",
    "autoresearch.stats.cli",
    "autoresearch.kb.cli",
    "autoresearch.runtime.card_cli",
    "autoresearch.comms.cli",
    "autoresearch.queue.cli",
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
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)
