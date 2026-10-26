"""The command-line contract every Pudge Wars Basic tool follows.

Output: with ``--json`` a tool prints exactly one JSON object on stdout:

    {"ok": true, "data": ...}
    {"ok": false, "error": {"reason": ..., "message": ...}, "data": ...?}

Everything else (progress, child-process output) goes to stderr. Without ``--json`` the
tool prints ``data`` as indented JSON on success and the error message on stderr.

Exit codes: 0 ok; 1 failed (the tool ran, the inputs or gates did not pass); 2 rejected
input (fix the arguments and retry); 3 errored (a tool or environment fault, such as a
missing Nim toolchain or a native build that cannot run).

Tools live in a hyphenated folder, so they import siblings by adding their own directory
to ``sys.path`` (``import cli_contract``), never through a ``games.<name>`` package path.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import traceback
from pathlib import Path

EXIT_OK, EXIT_FAILED, EXIT_REJECTED, EXIT_ERRORED = 0, 1, 2, 3
TOOLS = Path(__file__).resolve().parent
GAME = TOOLS.parent
ENGINE_ENV = TOOLS / 'engine.env'


class ToolError(Exception):
    """A tool outcome other than success; ``exit_code`` and ``reason`` name it."""

    exit_code = EXIT_ERRORED
    reason = 'errored'

    def __init__(self, message: str, *, reason: str | None = None, data=None):
        super().__init__(message)
        if reason:
            self.reason = reason
        self.data = data


class Rejected(ToolError):
    """The caller can correct this input and retry (exit 2)."""

    exit_code = EXIT_REJECTED
    reason = 'invalid_input'


class Failed(ToolError):
    """The tool ran and the subject did not pass (exit 1)."""

    exit_code = EXIT_FAILED
    reason = 'failed'


class Errored(ToolError):
    """A tool or environment fault (exit 3)."""


def engine() -> dict[str, str]:
    """The pinned engine (tools/engine.env) as a dict of strings."""
    values = {}
    for line in ENGINE_ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip().strip('"')
    return values


def jsonable(value):
    """Plain JSON values: paths become strings, non-finite floats become null."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class ArgumentParser(argparse.ArgumentParser):
    """argparse with ``--json``, the exit codes in ``--help``, and errors as Rejected."""

    def __init__(self, description: str | None = None, *, examples=(), json_flag=True, **kwargs):
        lines = ['examples:', *(f'  {example}' for example in examples)] if examples else []
        lines.append('output: --json prints one {"ok", "data"|"error"} object on stdout')
        lines.append('exit codes: 0 ok; 1 failed; 2 rejected input; 3 errored (tool or environment)')
        kwargs.setdefault('formatter_class', argparse.RawDescriptionHelpFormatter)
        super().__init__(description=description, epilog='\n'.join(lines), **kwargs)
        if json_flag:
            self.add_argument('--json', action='store_true',
                              help='print exactly one JSON envelope on stdout; other text goes to stderr')

    def error(self, message):
        raise Rejected(message, reason='usage_error')


@contextlib.contextmanager
def stdout_to_stderr():
    """Route every stdout write (Python and child processes) to stderr for a while."""
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)


def run(parser: ArgumentParser, body, argv: list[str] | None = None) -> int:
    """Parse argv, call ``body(args)`` for the data, print the envelope, return the exit code.

    ``body`` returns the data on success or raises a ToolError. Any other exception is a
    tool bug: it is reported as ``errored`` (exit 3) with the traceback on stderr.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = '--json' in argv
    if '-h' in argv or '--help' in argv:
        parser.parse_args(argv)  # prints help and exits 0
    envelope: dict
    code = EXIT_OK
    guard = stdout_to_stderr() if json_mode else contextlib.nullcontext()
    with guard:
        try:
            args = parser.parse_args(argv)
            envelope = {'ok': True, 'data': jsonable(body(args))}
        except ToolError as error:
            code = error.exit_code
            envelope = {'ok': False, 'error': {'reason': error.reason, 'message': str(error)}}
            if error.data is not None:
                envelope['data'] = jsonable(error.data)
            print(f'{parser.prog}: {error.reason}: {error}', file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - a crash still owes the caller one envelope
            traceback.print_exc()
            code = EXIT_ERRORED
            envelope = {'ok': False, 'error': {
                'reason': 'errored', 'message': f'{type(error).__name__}: {error} (tool bug; traceback on stderr)'}}
    if json_mode:
        print(json.dumps(envelope, allow_nan=False))
    elif envelope['ok'] or 'data' in envelope:
        print(json.dumps(envelope.get('data'), indent=2, allow_nan=False))
    return code
