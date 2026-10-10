"""``autoresearch runtime ...``: operator commands for canonical commit provenance
(``runtime.provenance``, C1).

- ``runtime check-commits``: read-only. ``{"violation": ..., "invalid_journal_lines": n}``:
  what queue-keeper's provenance check finds now (``violation`` null when the history is
  clean): each commit the runtime did not make, with its id,
  author, time and files, and the commit to give ``accept-commits``. It does not move the
  checked point and does not pause.
- ``runtime session``: read-only. ``{"agent_session": bool, "card": task id or null}``:
  whether this process carries the plugin's agent-session marker. Run from an agent's
  terminal after a deploy, it must say ``true``: ``accept-commits`` relies on it (README,
  "Update").
- ``runtime accept-commits <sha>``: operator only. Refused in a card context and in any agent
  session (``runctx.ENV_AGENT_SESSION``, set by the plugin), and blocked for agents by the
  plugin policy and the approvals deny list. Trust ``<sha>`` (HEAD or a commit
  in HEAD's history) and everything before it; queue-keeper's next run then checks only the
  commits after it and resumes the agent.

Prints one JSON envelope like ``card``: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"message": ...}}``. Exit codes: 0 done, 1 refused or a git failure,
2 bad arguments or no runtime context.
"""

import argparse
import json
import os

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.io import gitops
from autoresearch.runtime import provenance
from autoresearch.runtime.ledger import canonical_lock

EXIT_OK, EXIT_REFUSED, EXIT_BAD_ARGS = 0, 1, 2


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _fail(message: str, code: int) -> int:
    _emit({"ok": False, "error": {"message": message}})
    return code


def _run(handler):
    def run(args: argparse.Namespace) -> int:
        try:
            ctx = runctx.from_env()
        except KeyError as error:
            return _fail(f"{error.args[0]} is not set", EXIT_BAD_ARGS)
        try:
            data = handler(ctx, args)
        except (provenance.ProvenanceError, gitops.GitError, OSError) as error:
            return _fail(f"{type(error).__name__}: {error}", EXIT_REFUSED)
        _emit({"ok": True, "data": data})
        return EXIT_OK

    return run


def _check_commits(ctx, args) -> dict:
    with canonical_lock(ctx.canonical):
        violation = provenance.check(ctx.canonical, advance=False)
        invalid = provenance.invalid_lines(ctx.canonical)
    return {
        "violation": None if violation is None else violation.to_dict(),
        "invalid_journal_lines": invalid,
    }


def _session(ctx, args) -> dict:
    return {
        "agent_session": bool(os.environ.get(runctx.ENV_AGENT_SESSION)),
        "card": ctx.card_id,
    }


def _accept_commits(ctx, args) -> dict:
    if (
        ctx.card_id
        or ctx.worker
        or os.environ.get(runctx.ENV_WORKER)
        or os.environ.get(runctx.ENV_AGENT_SESSION)
    ):
        raise provenance.ProvenanceError(
            "accept-commits is for a human operator, not an agent session or a card: a commit "
            "the runtime did not make pauses the agent until a person checks it (D-27)"
        )
    with canonical_lock(ctx.canonical):
        return provenance.accept(ctx.canonical, args.sha)


def _setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    sub = commands.add_parser(
        "check-commits", help="list commits on canonical the runtime did not make"
    )
    sub.set_defaults(func=_run(_check_commits))
    sub = commands.add_parser(
        "session", help="whether this process runs in an agent session (the plugin's marker)"
    )
    sub.set_defaults(func=_run(_session))
    sub = commands.add_parser(
        "accept-commits", help="operator: trust a canonical commit and its history"
    )
    sub.add_argument("sha")
    sub.set_defaults(func=_run(_accept_commits))


register_group("runtime", _setup)
