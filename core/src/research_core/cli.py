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

# Module paths that register subcommands. Each is imported when the parser is built; a module
# that does not exist yet (an entry still being built) is skipped, so one missing piece never
# takes the whole CLI down, and `research --version` stays instant.
COMMAND_MODULES: tuple[str, ...] = (
    "research_core.contract.cli",
    "research_core.tools.cli",
    "research_core.store.cli",
    "research_core.platform.cli",
    "research_core.coworld_cli",
    "research_core.watcher.cli",
    "research_core.serve",
    "research_core.prepare_cli",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research", description="Softmax IDE research profiles")
    parser.add_argument("--version", action="version", version=f"research {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for module_name in dict.fromkeys(COMMAND_MODULES):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name and error.name.startswith("research_core"):
                continue  # not built yet
            raise
        module.add_parser(subparsers)
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
