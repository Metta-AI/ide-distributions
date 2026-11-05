"""The ``research`` command line.

Subcommands live in their own modules and register through ``COMMANDS``; each module exposes
``add_parser(subparsers)`` and sets ``parser.set_defaults(run=<callable>)``. The handler is called
with the parsed namespace and returns an exit code. Keep this module import-light: it is also
the entry point of tool subprocesses started from inside Hermes.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from research_core import __version__

# Module paths that register subcommands. Each is imported when the parser is built.
COMMAND_MODULES: tuple[str, ...] = (
    "research_core.contract.cli",
    "research_core.tools.cli",
    "research_core.store.cli",
    "research_core.platform.cli",
    "research_core.coworld_cli",
    "research_core.watcher.cli",
    "research_core.serve",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research", description="Softmax IDE research profiles")
    parser.add_argument("--version", action="version", version=f"research {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for module_name in COMMAND_MODULES:
        importlib.import_module(module_name).add_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run = getattr(args, "run", None)
    if run is None:
        parser.print_usage(sys.stderr)
        return 2
    return int(run(args) or 0)


if __name__ == "__main__":  # pragma: no cover - exercised through subprocesses
    sys.exit(main())
