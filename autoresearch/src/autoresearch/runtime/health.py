"""health: operational checks. A Hermes cron script job (``scripts/health.py`` ->
``python -m autoresearch.runtime.health``; ``cron/jobs.json`` runs it every 15 minutes).

Checks (Runtime §2, §8):

- Blocked cards (``blocked.handle_blocked``, shared with queue-keeper): requeue once, then a
  repair request and a runtime ``help_request``.
- Disk: less than 1 GB free under canonical.
- Credential: Softmax refused the credential or it is missing or malformed
  (``CredentialRejected``/``CredentialError``), it has expired, or it expires within 2 hours (a
  ``cga_`` agent token has no expiry). Any other ``whoami`` failure (network, 5xx) is not
  reported here: queue-keeper's preflight counts those and asks for help after 3 in a row.
  Identity mismatches are preflight's (queue-keeper pauses and asks for help, C6).

Each problem becomes one runtime ``help_request`` per kind (``disk``, ``whoami``,
``credential expiring``, ``credential expired``) per UTC day (its ``runtime_key`` carries the kind
and the date; the outbox is the dedupe state). The run ends with a push of ``main``. The job skips
quietly before bootstrap has created the canonical workspace, and runs one at a time
(``runtime/health.run.lock``). Failures are logged to ``.supervisor/runtime/health.log``.
"""

import datetime
import json
import os
import shutil
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch import runctx
from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io import gitops
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.queue.base import Queue
from autoresearch.queue.select import queue_from_env
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.blocked import BlockedAction, handle_blocked
from autoresearch.runtime.ledger import canonical_lock
from autoresearch.runtime.preflight import EXPIRY_WARNING, MIN_FREE_BYTES
from autoresearch.softmax_tool.identity import CredentialRejected
from autoresearch.softmax_tool.platform_cli import CredentialError
from autoresearch.softmax_tool.protocol import Softmax
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env

LOG_FILE = "health.log"
RUN_LOCK = "health.run.lock"
REMOTE = "origin"


@dataclass
class HealthRun:
    skipped: bool = False  # no canonical yet, or another run held the lock
    blocked: list[BlockedAction] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)  # titles of new help_requests
    failures: dict[str, str] = field(default_factory=dict)


def _log(ctx: RunContext, message: str) -> None:
    path = runtime_dir(ctx) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def disk_problem(canonical: Path, disk_free: Callable) -> str | None:
    try:
        free = disk_free(canonical).free
    except OSError as error:
        return f"disk: cannot read free space under {canonical}: {error}"
    if free < MIN_FREE_BYTES:
        return f"disk: only {free // 1024**2} MB free under {canonical}; need 1024 MB"
    return None


def credential_problem(sm: Softmax, now: datetime.datetime) -> tuple[str, str] | None:
    """(kind, problem) for the credential, or None when it is fine for 2 more hours."""
    try:
        identity = sm.whoami()
    except (CredentialRejected, CredentialError) as error:
        return "whoami", f"whoami failed: {type(error).__name__}: {error}"
    except Exception:  # noqa: BLE001 - not a verdict on the credential; preflight counts these
        return None
    if identity.expires_at is None:  # a cga_ agent token does not expire
        return None
    if identity.expires_at <= now:
        return "credential expired", f"the credential expired at {identity.expires_at.isoformat()}"
    if identity.expires_at - now < EXPIRY_WARNING:
        return "credential expiring", f"the credential expires at {identity.expires_at.isoformat()}"
    return None


def _report(
    ctx: RunContext, kind: str, problem: str, now: datetime.datetime, result: HealthRun
) -> None:
    """One help_request per problem kind per UTC day."""
    canonical = ctx.canonical
    message = runtime_messages.health_problem(
        kind, problem, f"{now.astimezone(datetime.UTC):%Y-%m-%d}"
    )
    with canonical_lock(canonical):
        if already_published(canonical, message):
            return
        publish_message(canonical, message, allow_no_remote=True, expects_response=True)
    result.published.append(message.title)


def run(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    now: datetime.datetime,
    *,
    env: Mapping[str, str],
    disk_free: Callable = shutil.disk_usage,
) -> HealthRun:
    """One health run (module docstring). ``env`` is accepted for symmetry with the other
    jobs; ``disk_free`` is the test seam."""
    del env
    result = HealthRun()
    if not (ctx.canonical / ".git").exists():
        result.skipped = True
        return result
    try:
        with file_lock(runtime_dir(ctx) / RUN_LOCK, timeout_s=0):
            try:
                result.blocked = handle_blocked(ctx, queue, allow_no_remote=True)
            except Exception as error:  # noqa: BLE001 - the other checks still run
                result.failures["blocked"] = f"{type(error).__name__}: {error}"
                _log(ctx, f"blocked check failed: {error}\n{traceback.format_exc()}")
            found = []
            disk = disk_problem(ctx.canonical, disk_free)
            if disk:
                found.append(("disk", disk))
            credential = credential_problem(sm, now)
            if credential:
                found.append(credential)
            for kind, problem in found:
                result.problems.append(problem)
                _log(ctx, f"problem: {problem}")
                try:
                    _report(ctx, kind, problem, now, result)
                except Exception as error:  # noqa: BLE001 - e.g. the disk is too full to commit
                    result.failures[kind] = f"{type(error).__name__}: {error}"
                    _log(ctx, f"could not report {kind}: {error}")
            _push(ctx)
    except LockTimeout:
        result.skipped = True
    return result


def _push(ctx: RunContext) -> None:
    """Push the run's commits (markers, requests); queue-keeper owns push failure reports."""
    if REMOTE in gitops.git(ctx.canonical, "remote").split():
        with canonical_lock(ctx.canonical):
            if not gitops.push(ctx.canonical, REMOTE, ["HEAD:refs/heads/main"]):
                _log(ctx, "push of main failed; queue-keeper retries it")


def main(argv: list[str] | None = None) -> int:
    del argv
    env = os.environ
    try:
        ctx = runctx.from_env(env)
        queue = queue_from_env(env, ctx)
        sm = softmax_from_env(env, ctx.canonical)
    except Exception as error:  # noqa: BLE001 - report any setup failure and fail the job
        print(f"health: cannot start: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    result = run(ctx, queue, sm, datetime.datetime.now(datetime.UTC), env=env)
    summary = {
        "skipped": result.skipped,
        "blocked": [(action.task_id, action.action) for action in result.blocked],
        "problems": result.problems,
        "published": result.published,
        "failures": result.failures,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
