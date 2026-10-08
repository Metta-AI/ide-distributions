#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_cli.py
"""The agent CLI contract shared by every Paintbot PW lab tool (one JSON envelope, fixed exit codes).

Every Python tool builds its parser with `ArgumentParser` and runs its work function through `run`:

    parser = pw_cli.ArgumentParser("pw_metrics", __doc__, examples=["... ROOT --json"])
    parser.add_argument(...)

    def run_cli(args, report):       # report: the envelope being filled
        batch = pw_episodes.load_batch(args.roots, tag=args.tag)
        report.add_batch(batch)      # processed/failed counts + one failure per episode
        pw_cli.require_policy(batch.episodes, args.policy)   # exit 2 listing valid values
        return {"...": ...}          # the envelope's `result`

    def main(argv=None):
        return pw_cli.run(parser, run_cli, argv)

Contract (docs/tools/README.md "Agent contract"):
  --json   exactly ONE JSON object on stdout; every other line the tool prints (including
           native code and child processes) goes to stderr. Keys: ok, tool, release_tag,
           inputs, outputs, counts, failures, result, next.
  exit 0   success
  exit 1   some inputs failed verification or loading (partial results written, failures listed)
  exit 2   usage or config error, including unknown selectors (the message lists valid values)
  exit 3   environment missing: a binary, library or release not built; the message names
           the command that fixes it
A rate limit that outlasted the retries (HTTP 429) is NOT an environment problem: failure code
"rate_limited", exit 1, `next` = wait and retry (raise pw_cli.RateLimited).
An unexpected exception (a tool bug) still prints the envelope, with failure code "crash"
and exit 1, and the traceback on stderr.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_release  # noqa: E402

EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, EXIT_ENVIRONMENT = 0, 1, 2, 3
LAB = pw_release.LAB
# Default home for files a tool writes when no --out is given: <lab>/analysis/<tool>/...
# (`analysis/` is gitignored). Paths under it are a pure function of the inputs, so a re-run
# overwrites the same files instead of piling up new ones.
ANALYSIS = LAB / "analysis"


class UsageError(SystemExit):
    """Bad arguments or an unknown selector: exit 2. `valid` lists the accepted values.

    A SystemExit so a plain script that is not wrapped by `run` still stops with the message."""

    exit_code = EXIT_USAGE

    def __init__(self, message: str, valid=None):
        self.valid = [str(v) for v in valid] if valid is not None else None
        if self.valid is not None:
            shown = self.valid if len(self.valid) <= 40 else self.valid[:40] + [f"... ({len(self.valid)} in all)"]
            message = f"{message}; valid: {', '.join(shown) or '(none)'}"
        super().__init__(message)
        self.message = message


class EnvironmentMissing(SystemExit):
    """Something outside the tool is missing or broken: exit 3. `fix` is the command that fixes it."""

    exit_code = EXIT_ENVIRONMENT

    def __init__(self, message: str, fix: str):
        super().__init__(f"{message}: run {fix}")
        self.message, self.fix = f"{message}: run {fix}", fix


class RateLimited(SystemExit):
    """A remote API kept answering HTTP 429 after polite retries: exit 1, failure code
    rate_limited. Nothing is missing; `retry` is the command to run again after a wait."""

    exit_code = EXIT_PARTIAL

    def __init__(self, message: str, retry: str):
        super().__init__(message)
        self.message, self.retry = message, retry


def jsonable(value):
    """Plain JSON types from pandas/numpy/Path values; NaN becomes null."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):   # numpy scalar
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if hasattr(value, "tolist"):                                        # numpy array
        return jsonable(value.tolist())
    if hasattr(value, "isoformat"):                                     # datetime / Timestamp
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    return str(value)


def records(frame) -> list[dict]:
    """A DataFrame as a list of JSON-ready row dicts."""
    return [jsonable(row) for row in frame.to_dict(orient="records")] if frame is not None and len(frame) else []


class Report:
    """The envelope of one CLI run. Tools fill it; `run` prints it and picks the exit code."""

    def __init__(self, tool: str, json_mode: bool = False):
        self.tool = tool
        self.json_mode = json_mode
        self.release_tag = _release_tag()
        self.inputs: dict = {}
        self.outputs: list[str] = []
        self.counts: dict = {"processed": 0, "failed": 0, "excluded": 0}
        self.failures: list[dict] = []
        self.result = None
        self.next: list[str] = []
        self.exit_code: int | None = None

    def fail(self, id: str, code: str, message: str) -> None:  # noqa: A002 - envelope field name
        self.failures.append({"id": str(id), "code": str(code), "message": str(message)})

    def output(self, path) -> str:
        path = str(path)
        if path not in self.outputs:
            self.outputs.append(path)
        return path

    def suggest(self, command: str) -> None:
        if command not in self.next:
            self.next.append(command)

    def add_batch(self, batch) -> None:
        """Counts, failures and newly built trace caches from a pw_episodes.Batch (a failed
        episode is exit 1; every cache this run traced goes into `outputs`).

        A batch with no episode at all (none loaded, none failed) is a usage error: the roots
        point at nothing, and an empty result must not look like success."""
        if not batch.episodes and not batch.failures:
            raise UsageError("no episode found under the given roots (an episode is a directory with "
                             "episode.json or results.json plus a tape, or a NAME.replay file)")
        self.counts["processed"] += len(batch.episodes)
        for episode in batch.episodes:
            if not episode.cache_hit:
                self.output(episode.source.cache)
        for path, code, message in batch.failures:
            self.fail(path, code, message)
        if batch.failures:
            self.counts["failed_by_code"] = dict(batch.exclusions)

    def envelope(self, code: int) -> dict:
        counts = dict(self.counts)
        counts["failed"] = max(counts.get("failed", 0), len(self.failures))
        return {"ok": code == EXIT_OK, "tool": self.tool, "release_tag": self.release_tag,
                "inputs": jsonable(self.inputs), "outputs": self.outputs, "counts": jsonable(counts),
                "failures": self.failures, "result": jsonable(self.result), "next": self.next}


def _release_tag() -> str | None:
    try:
        return pw_release.current_tag()
    except (OSError, ValueError):
        return None


class ArgumentParser(argparse.ArgumentParser):
    """argparse with the contract: a --json flag, examples and exit codes in --help, and
    argument errors raised as UsageError (exit 2, an envelope in --json mode)."""

    def __init__(self, tool: str | None = None, description: str | None = None, *, examples=(),
                 exit_codes: str | None = None, json_flag: bool = True, **kwargs):
        # argparse builds subcommand parsers with this class too (tool=None, a --json flag each).
        epilog = kwargs.pop("epilog", None) or _epilog(examples, exit_codes)
        kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        super().__init__(description=description, epilog=epilog, **kwargs)
        self.tool = tool
        if json_flag:
            add_json_flag(self)

    def error(self, message: str):
        raise UsageError(message)


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="print exactly one JSON object (the agent contract envelope) to stdout; "
                             "human-readable text goes to stderr")


def _epilog(examples, exit_codes) -> str:
    lines = []
    if examples:
        lines += ["examples:", *(f"  {e}" for e in examples)]
    lines.append("exit codes: " + (exit_codes or "0 ok; 1 some inputs failed (partial results, failures listed); "
                                   "2 usage error / unknown selector; 3 environment missing (message names the fix)"))
    return "\n".join(lines)


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


def _inputs(args: argparse.Namespace) -> dict:
    return {k: v for k, v in vars(args).items() if k not in ("json", "func") and not callable(v)}


def run(parser: ArgumentParser, body, argv: list[str] | None = None) -> int:
    """Parse argv, run body(args, report) and finish the contract: print the envelope in
    --json mode and return the exit code. body returns the envelope's `result`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    report = Report(parser.tool, json_mode="--json" in argv)
    guard = stdout_to_stderr() if report.json_mode else contextlib.nullcontext()
    code = EXIT_OK
    with guard:
        try:
            args = parser.parse_args(argv)
            report.inputs = _inputs(args)
            if getattr(args, "tag", None):
                report.release_tag = args.tag
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
        except RateLimited as err:
            report.fail("rate_limit", "rate_limited", err.message)
            report.suggest("wait and retry")
            report.suggest(err.retry)
            print(f"ERROR: {err.message} (rate limited; nothing is missing: wait, then run {err.retry})",
                  file=sys.stderr)
            code = EXIT_PARTIAL
        except pw_release.NotBuilt as err:
            report.fail(err.tool, "not_built", str(err))
            report.suggest(err.fix)
            print(f"ERROR: {err}", file=sys.stderr)
            code = EXIT_ENVIRONMENT
        except SystemExit as err:
            if err.code in (None, 0):                   # --help
                if not report.json_mode:
                    raise
                code = EXIT_OK
            elif isinstance(err.code, int):
                report.fail("tool", "exit", f"exited {err.code}")
                code = err.code
            else:                                       # sys.exit("message"): a refusal, i.e. a config error
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


# ---------------------------------------------------------------- shared selector checks

def policy_values(episodes) -> list[str]:
    """Every policy_key and policy_name in the episodes' seats tables (sorted)."""
    values = set()
    for ep in episodes:
        seats = ep["seats"]
        values |= {str(v) for v in seats.policy_key.dropna()} | {str(v) for v in seats.policy_name.dropna()}
    return sorted(values)


def require_policy(episodes, policy: str | None, flag: str = "--policy") -> None:
    """UsageError (exit 2, listing valid values) unless `policy` is a key or name in the episodes.

    No check when nothing loaded: the load failures are the finding then."""
    if not policy or not episodes:
        return
    values = policy_values(episodes)
    if policy not in values:
        raise UsageError(f"{flag} {policy!r} is not in these episodes (policy_key or policy_name)", values)


def default_out(tool: str, *parts: str) -> Path:
    """<lab>/analysis/<tool>/<parts...>: the documented default output location."""
    return ANALYSIS.joinpath(tool, *parts)
