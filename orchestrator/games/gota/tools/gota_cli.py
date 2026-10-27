#!/usr/bin/env python3
# Source: personal_labs_gota@09d3ed28:gods_of_the_arena_lab/tools/gota_cli.py
"""The agent CLI contract shared by the GotA strategy tools: one JSON envelope, fixed exit codes.

Ported from paintbot_pw_lab/tools/pw_cli.py without its release coupling. Usage:

    parser = gota_cli.ArgumentParser("gota_strategy", __doc__, examples=["... lint --json"])
    def run_cli(args, report):
        return {...}                 # the envelope's `result`
    def main(argv=None):
        return gota_cli.run(parser, run_cli, argv)

Contract:
  --json   exactly ONE JSON object on stdout; every other line (including child processes) goes
           to stderr. Keys: ok, tool, engine, inputs, outputs, counts, failures, result, next.
  exit 0   success
  exit 1   some inputs or gates failed (partial results written, failures listed)
  exit 2   usage or config error
  exit 3   environment missing: a binary or tool not built; the message names the fixing command
An unexpected exception still prints the envelope, with failure code "crash" and exit 1.
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

EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3
LAB = Path(__file__).resolve().parents[1]
REPO = LAB.parent
ENGINE_ENV = LAB / "tools" / "engine.env"


class UsageError(SystemExit):
    """Bad arguments or refused input: exit 2. `valid` lists the accepted values."""

    exit_code = EXIT_USAGE

    def __init__(self, message: str, valid=None):
        self.valid = [str(v) for v in valid] if valid is not None else None
        if self.valid is not None:
            message = f"{message}; valid: {', '.join(self.valid) or '(none)'}"
        super().__init__(message)
        self.message = message


class EnvironmentMissing(SystemExit):
    """Something outside the tool is missing: exit 3. `fix` is the command that fixes it."""

    exit_code = EXIT_ENVIRONMENT

    def __init__(self, message: str, fix: str):
        super().__init__(f"{message}: run {fix}")
        self.message, self.fix = f"{message}: run {fix}", fix


def engine() -> dict[str, str]:
    """The pinned engine (tools/engine.env): POLYWORLD_COMMIT, BASSY_COMMIT, COWORLD_VERSION, COWORLD_ID."""
    values = {}
    for line in ENGINE_ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def jsonable(value):
    """Plain JSON types from Path/dataclass-free values; NaN becomes null."""
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


class Report:
    """The envelope of one CLI run. Tools fill it; `run` prints it and picks the exit code."""

    def __init__(self, tool: str, json_mode: bool = False):
        self.tool = tool
        self.json_mode = json_mode
        try:
            self.engine = engine().get("POLYWORLD_COMMIT")
        except OSError:
            self.engine = None
        self.inputs: dict = {}
        self.outputs: list[str] = []
        self.counts: dict = {"processed": 0, "failed": 0}
        self.failures: list[dict] = []
        self.result = None
        self.next: list[str] = []
        self.exit_code: int | None = None

    def fail(self, id: str, code: str, message: str) -> None:
        self.failures.append({"id": str(id), "code": str(code), "message": str(message)})

    def output(self, path) -> str:
        path = str(path)
        if path not in self.outputs:
            self.outputs.append(path)
        return path

    def suggest(self, command: str) -> None:
        if command not in self.next:
            self.next.append(command)

    def envelope(self, code: int) -> dict:
        counts = dict(self.counts)
        counts["failed"] = max(counts.get("failed", 0), len(self.failures))
        return {"ok": code == EXIT_OK, "tool": self.tool, "engine": self.engine,
                "inputs": jsonable(self.inputs), "outputs": self.outputs, "counts": jsonable(counts),
                "failures": self.failures, "result": jsonable(self.result), "next": self.next}


class ArgumentParser(argparse.ArgumentParser):
    """argparse with the contract: a --json flag, examples and exit codes in --help, and argument
    errors raised as UsageError (exit 2, an envelope in --json mode)."""

    def __init__(self, tool: str | None = None, description: str | None = None, *, examples=(), **kwargs):
        lines = ["examples:", *(f"  {e}" for e in examples)] if examples else []
        lines.append("exit codes: 0 ok; 1 some inputs or gates failed; 2 usage error; 3 environment missing")
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(description=description, epilog=kwargs.pop("epilog", None) or "\n".join(lines), **kwargs)
        self.tool = tool
        self.add_argument("--json", action="store_true",
                          help="print exactly one JSON object (the envelope) to stdout; other text goes to stderr")

    def error(self, message: str):
        raise UsageError(message)


@contextlib.contextmanager
def stdout_to_stderr():
    """Send every write to stdout (Python, native code, child processes) to stderr for a while."""
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
    """Parse argv, run body(args, report), print the envelope in --json mode, return the exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    report = Report(parser.tool, json_mode="--json" in argv)
    guard = stdout_to_stderr() if report.json_mode else contextlib.nullcontext()
    code = EXIT_OK
    with guard:
        try:
            args = parser.parse_args(argv)
            report.inputs = {k: v for k, v in vars(args).items() if k != "json" and not callable(v)}
            report.result = body(args, report)
            code = report.exit_code if report.exit_code is not None else (EXIT_PARTIAL if report.failures else EXIT_OK)
        except UsageError as err:
            report.fail("usage", "usage_error", err.message)
            if err.valid is not None:
                report.result = {"valid": err.valid}
            print(f"{parser.prog}: error: {err.message}", file=sys.stderr)
            code = EXIT_USAGE
        except EnvironmentMissing as err:
            report.fail("environment", "environment_missing", err.message)
            report.suggest(err.fix)
            print(f"ERROR: {err.message}", file=sys.stderr)
            code = EXIT_ENVIRONMENT
        except SystemExit as err:
            if err.code in (None, 0):
                if not report.json_mode:
                    raise
                code = EXIT_OK
            elif isinstance(err.code, int):
                report.fail("tool", "exit", f"exited {err.code}")
                code = err.code
            else:
                report.fail("tool", "usage_error", err.code)
                print(err.code, file=sys.stderr)
                code = EXIT_USAGE
        except Exception as err:  # noqa: BLE001 - a crash still owes the caller its one envelope
            traceback.print_exc()
            report.fail("crash", "crash", f"{type(err).__name__}: {err} (a tool bug; traceback on stderr)")
            code = EXIT_PARTIAL
    if report.json_mode:
        print(json.dumps(report.envelope(code)))
    return code
